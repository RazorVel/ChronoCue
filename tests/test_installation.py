"""Exercise setup in temporary homes without contacting the real user manager."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class InstallationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="chronocue-setup-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home with spaces"
        self.home.mkdir()
        self.bin = self.root / "test-bin"
        self.bin.mkdir()
        self.log = self.root / "systemctl.jsonl"
        self.env = os.environ.copy()
        for name in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "CHRONOCUE_CONFIG", "PYTHONPATH"):
            self.env.pop(name, None)
        self.env.update(
            HOME=str(self.home),
            PATH=str(self.bin) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
            TEST_SYSTEMCTL_LOG=str(self.log),
        )
        self.stub("python3", """
            import json
            import os
            import sys
            if sys.argv[1] == '-c':
                check = 'tk' if 'tkinter' in sys.argv[2] else 'version'
                sys.exit(1 if os.environ.get('TEST_PYTHON_FAIL') == check else 0)
            print(json.dumps({
                'args': sys.argv[1:],
                'config': os.environ.get('CHRONOCUE_CONFIG'),
                'source': os.environ.get('PYTHONPATH'),
                'state': os.environ.get('XDG_STATE_HOME'),
                'cache': os.environ.get('XDG_CACHE_HOME'),
            }))
        """)
        self.stub("systemctl", """
            import json
            import os
            import sys
            with open(os.environ['TEST_SYSTEMCTL_LOG'], 'a') as stream:
                stream.write(json.dumps(sys.argv[1:]) + '\\n')
            command = sys.argv[2]
            if command == os.environ.get('TEST_SYSTEMCTL_FAIL'):
                sys.exit(1)
            if command == 'show':
                print(os.environ.get('TEST_LOAD_STATE', 'loaded'))
        """)
        self.stub("notify-send", "pass")
        self.stub("paplay", "pass")

    def stub(self, name, source):
        path = self.bin / name
        path.write_text("#!" + sys.executable + "\n" + textwrap.dedent(source))
        path.chmod(0o755)

    def run_script(self, name, *arguments):
        return subprocess.run(
            ["/bin/bash", str(PROJECT_ROOT / "scripts" / name), *arguments],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def install(self):
        result = self.run_script("install.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def launcher(self, name="daemon", *arguments, env=None):
        result = subprocess.run(
            [str(self.home / ".local/bin" / ("chronocue-" + name)), *arguments],
            cwd=self.root,
            env=self.env if env is None else env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    @property
    def config(self):
        return self.home / ".config/chronocue/config.json"

    @property
    def app(self):
        return self.home / ".local/share/chronocue"

    def test_fresh_install_and_launchers(self):
        self.install()
        self.assertEqual(json.loads(self.config.read_text())["schedules"], [])
        self.assertTrue((self.app / "src/chronocue/daemon.py").is_file())
        self.assertEqual(
            self.calls(),
            [
                ["--user", "show-environment"],
                ["--user", "daemon-reload"],
                ["--user", "enable", str(self.home / ".config/systemd/user/chronocue.service")],
                ["--user", "restart", "chronocue.service"],
                ["--user", "is-active", "--quiet", "chronocue.service"],
            ],
        )
        for module in ("daemon", "ui"):
            with self.subTest(module=module):
                launch = self.launcher(module, "--config", "manual schedule.json")
                self.assertEqual(launch["args"], ["-m", "chronocue." + module, "--config", "manual schedule.json"])
                self.assertEqual(launch["config"], str(self.config))
                self.assertEqual(launch["source"], str(self.app / "src"))
                self.assertEqual(launch["state"], str(self.home / ".local/state"))
                self.assertEqual(launch["cache"], str(self.home / ".cache"))

    def test_reinstall_preserves_schedule_replaces_source_and_restarts(self):
        self.install()
        custom = '{"schedules": [{"id": "keep-me"}]}\n'
        self.config.write_text(custom)
        obsolete = self.app / "src/obsolete.py"
        obsolete.touch()
        self.install()
        self.assertEqual(self.config.read_text(), custom)
        self.assertFalse(obsolete.exists())
        self.assertEqual(self.calls().count(["--user", "restart", "chronocue.service"]), 2)
        self.assertEqual(list(self.app.glob(".install.*")), [])

    def test_special_character_paths_are_literal_and_config_is_persisted(self):
        self.env["XDG_DATA_HOME"] = str(self.root / 'data $(touch INJECTED) `touch ALSO_INJECTED` "quoted"')
        self.env["XDG_CONFIG_HOME"] = str(self.root / 'config $literal "quoted"')
        self.env["XDG_STATE_HOME"] = str(self.root / "custom-state")
        self.env["XDG_CACHE_HOME"] = str(self.root / "custom-cache")
        self.install()
        launch_env = self.env.copy()
        launch_env.pop("XDG_DATA_HOME")
        launch_env.pop("XDG_CONFIG_HOME")
        launch_env.pop("XDG_STATE_HOME")
        launch_env.pop("XDG_CACHE_HOME")
        for module in ("daemon", "ui"):
            launch = self.launcher(module, env=launch_env)
            self.assertEqual(launch["source"], self.env["XDG_DATA_HOME"] + "/chronocue/src")
            self.assertEqual(launch["config"], self.env["XDG_CONFIG_HOME"] + "/chronocue/config.json")
            self.assertEqual(launch["state"], self.env["XDG_STATE_HOME"])
            self.assertEqual(launch["cache"], self.env["XDG_CACHE_HOME"])
        self.assertFalse((self.root / "INJECTED").exists())
        self.assertFalse((self.root / "ALSO_INJECTED").exists())

    def test_relative_custom_config_becomes_absolute_default_with_runtime_override(self):
        self.env["CHRONOCUE_CONFIG"] = "custom schedules/reminders.json"
        self.install()
        config = self.root / "custom schedules/reminders.json"
        self.assertTrue(config.is_file())
        launch_env = self.env.copy()
        launch_env.pop("CHRONOCUE_CONFIG")
        self.assertEqual(self.launcher(env=launch_env)["config"], str(config))
        launch_env["CHRONOCUE_CONFIG"] = "/other/explicit.json"
        self.assertEqual(self.launcher(env=launch_env)["config"], "/other/explicit.json")
        self.assertFalse(self.config.exists())

    def test_legacy_default_config_is_migrated_without_removing_original(self):
        legacy = self.home / ".config/chronocue/schedule.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{"schedules": [{"id": "keep-me"}]}\n')

        result = self.run_script("install.sh")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.config.read_text(), legacy.read_text())
        self.assertTrue(legacy.is_file())
        self.assertIn("Migrated existing schedule", result.stdout)

    def test_python_requirements_fail_before_mutation(self):
        for check, message in (("version", "Python 3.10"), ("tk", "Missing tkinter")):
            with self.subTest(check=check):
                self.env["TEST_PYTHON_FAIL"] = check
                result = self.run_script("install.sh")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertEqual(list(self.home.iterdir()), [])
                self.assertEqual(self.calls(), [])

    def test_unavailable_manager_fails_before_mutation(self):
        self.env["TEST_SYSTEMCTL_FAIL"] = "show-environment"
        result = self.run_script("install.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_relative_xdg_directories_are_rejected_before_mutation(self):
        for variable in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
            with self.subTest(variable=variable):
                self.env[variable] = "relative-directory"
                result = self.run_script("install.sh")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("absolute paths", result.stderr)
                self.assertEqual(list(self.home.iterdir()), [])
                del self.env[variable]

    def test_failed_service_start_is_reported(self):
        self.env["TEST_SYSTEMCTL_FAIL"] = "is-active"
        result = self.run_script("install.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not stay active", result.stderr)
        self.assertNotIn("Installation complete", result.stdout)

    def test_uninstall_preserves_schedule(self):
        self.install()
        result = self.run_script("uninstall.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.config.is_file())
        self.assertFalse(self.app.exists())
        self.assertFalse((self.home / ".local/bin/chronocue-daemon").exists())
        self.assertFalse((self.home / ".config/systemd/user/chronocue.service").exists())
        self.assertLess(
            self.calls().index(["--user", "stop", "chronocue.service"]),
            self.calls().index(["--user", "disable", "chronocue.service"]),
        )

    def test_uninstall_purge_removes_default_config_but_preserves_external_file(self):
        self.install()
        external = self.root / "external-schedule.json"
        external.write_text("important")
        self.env["CHRONOCUE_CONFIG"] = str(external)
        result = self.run_script("uninstall.sh", "--purge")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.config.parent.exists())
        self.assertEqual(external.read_text(), "important")

    def test_uninstall_invalid_arguments_do_nothing(self):
        self.install()
        previous_calls = self.calls()
        for arguments in (("--pruge",), ("--purge", "extra")):
            with self.subTest(arguments=arguments):
                result = self.run_script("uninstall.sh", *arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(self.calls(), previous_calls)
                self.assertTrue(self.app.exists())
                self.assertTrue(self.config.exists())

    def test_uninstall_failed_stop_preserves_everything(self):
        self.install()
        self.env["TEST_SYSTEMCTL_FAIL"] = "stop"
        result = self.run_script("uninstall.sh", "--purge")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no files were removed", result.stderr)
        self.assertTrue(self.app.exists())
        self.assertTrue(self.config.exists())
        self.assertTrue((self.home / ".local/bin/chronocue-daemon").exists())
        self.assertTrue((self.home / ".config/systemd/user/chronocue.service").exists())
        self.assertNotIn(["--user", "disable", "chronocue.service"], self.calls())

    def test_uninstall_missing_unit_still_removes_files(self):
        self.install()
        self.env["TEST_LOAD_STATE"] = "not-found"
        self.env["TEST_SYSTEMCTL_FAIL"] = "stop"
        result = self.run_script("uninstall.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.app.exists())
        self.assertNotIn(["--user", "stop", "chronocue.service"], self.calls())

    def test_uninstall_reload_failure_does_not_skip_purge(self):
        self.install()
        self.env["TEST_SYSTEMCTL_FAIL"] = "daemon-reload"
        result = self.run_script("uninstall.sh", "--purge")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Files removed, but systemd reload failed", result.stderr)
        self.assertFalse(self.config.parent.exists())
        self.assertFalse(self.app.exists())


if __name__ == "__main__":
    unittest.main()
