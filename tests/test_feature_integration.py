"""Exercise preset activation, timer completion, and audio in the real daemon."""

from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import chronocue
from chronocue.config import save_config, validate_config
from chronocue.pomodoro import PomodoroStore


class FeatureIntegrationTest(unittest.TestCase):
    def test_background_pomodoro_and_preset_audio(self):
        with tempfile.TemporaryDirectory(prefix='chronocue-features-') as temporary:
            root = Path(temporary)
            binary_dir = root / 'bin'
            binary_dir.mkdir()
            notification_log, audio_log = root / 'notifications.jsonl', root / 'audio.jsonl'
            for name, log, line in (
                ('notify-send', notification_log, "sys.argv[-2]"),
                ('paplay', audio_log, "os.path.basename(sys.argv[-1])"),
            ):
                executable = binary_dir / name
                executable.write_text(
                    f'#!{sys.executable}\nimport sys, os, json\n'
                    f'with open({str(log)!r}, "a") as output:\n'
                    f'    output.write(json.dumps({line}) + "\\n")\n'
                )
                executable.chmod(0o700)
            env = os.environ.copy()
            env.update(PATH=str(binary_dir), XDG_STATE_HOME=str(root / 'state'), XDG_CACHE_HOME=str(root / 'cache'),
                       PYTHONPATH=str(Path(chronocue.__file__).resolve().parent.parent), PYTHONDONTWRITEBYTECODE='1')
            config_path = root / 'schedule.json'
            config = validate_config({
                'settings': {'poll_seconds': 1, 'max_late_seconds': 86400, 'sound_enabled': True},
                'presets': [{'id': 'work', 'name': 'Work', 'enabled': False}],
                'schedules': [{'id': 'one', 'title': 'Group reminder', 'time': datetime.now().strftime('%H:%M'), 'preset_id': 'work', 'ringtone': 'radar'}],
                'pomodoro': {'focus_minutes': 1, 'ringtone': 'little-victory'},
            })
            save_config(config, config_path)
            with patch.dict(os.environ, {'XDG_STATE_HOME': env['XDG_STATE_HOME']}):
                store = PomodoroStore.for_config(config_path)
            store.command('start', config['pomodoro'], now=time.time() - 65)

            def read_log(path):
                if not path.exists():
                    return []
                return [json.loads(line) for line in path.read_text().splitlines() if line]

            log_path = root / 'daemon.log'
            with log_path.open('w') as log:
                process = subprocess.Popen([sys.executable, '-m', 'chronocue.daemon', '--config', str(config_path)], env=env, stdout=log, stderr=log)
                try:
                    def wait_for(predicate):
                        deadline = time.monotonic() + 10
                        while time.monotonic() < deadline:
                            self.assertIsNone(process.poll(), log_path.read_text())
                            if predicate():
                                return
                            time.sleep(.05)
                        self.fail('Timed out: ' + log_path.read_text())
                    wait_for(lambda: len(read_log(audio_log)) == 1)
                    self.assertEqual(read_log(notification_log), ['[ChronoCue] Focus complete'])
                    self.assertIn('little-victory', read_log(audio_log)[0])
                    self.assertEqual(store.snapshot(config['pomodoro'])['status'], 'ready')
                    config['presets'][0]['enabled'] = True
                    save_config(config, config_path)
                    wait_for(lambda: len(read_log(audio_log)) == 2)
                    self.assertEqual(read_log(notification_log), ['[ChronoCue] Focus complete', '[ChronoCue] Group reminder'])
                    self.assertIn('radar', read_log(audio_log)[1])
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)

    def test_countdown_alert_sound_and_no_repeat_after_daemon_restart(self):
        from chronocue.clocks import CountdownStore
        with tempfile.TemporaryDirectory(prefix='chronocue-countdown-') as temporary:
            root = Path(temporary)
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            notification_log, sound_log = root / 'notify.jsonl', root / 'audio.jsonl'
            for name, output in (('notify-send', notification_log), ('paplay', sound_log)):
                executable = bin_dir / name
                executable.write_text(f'#!{sys.executable}\nimport sys,json\n'
                                      f'with open({str(output)!r}, "a") as output: output.write(json.dumps(sys.argv[1:]) + "\\n")\n')
                executable.chmod(0o700)
            env = {**os.environ, 'PATH': str(bin_dir), 'XDG_STATE_HOME': str(root / 'state'),
                   'XDG_CACHE_HOME': str(root / 'cache'), 'PYTHONDONTWRITEBYTECODE': '1',
                   'PYTHONPATH': str(Path(chronocue.__file__).resolve().parent.parent)}
            path = root / 'schedule.json'
            config = validate_config({'settings': {'poll_seconds': 3600},
                                      'countdown': {'duration_seconds': 1, 'ringtone': 'radar'}})
            save_config(config, path)
            with patch.dict(os.environ, {'XDG_STATE_HOME': env['XDG_STATE_HOME']}):
                store = CountdownStore.for_config(path)
            store.command('start', config['countdown'])
            process = None
            with (root / 'daemon.log').open('w') as log:
                def launch():
                    return subprocess.Popen([sys.executable, '-m', 'chronocue.daemon', '--config', str(path)],
                                            env=env, stdout=log, stderr=log)
                def stop(process):
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                try:
                    process = launch()
                    deadline = time.monotonic() + 8
                    while (not sound_log.exists() or not sound_log.read_text().strip() or store.snapshot().get('pending_alert') is not None) and time.monotonic() < deadline:
                        self.assertIsNone(process.poll())
                        time.sleep(.05)
                    self.assertTrue(sound_log.exists(), (root / 'daemon.log').read_text())
                    args = json.loads(notification_log.read_text().splitlines()[0])
                    self.assertEqual(args[args.index('--expire-time') + 1], '0')
                    self.assertEqual(args[-2], '[ChronoCue] Timer complete')
                    self.assertIn('radar-80.wav', sound_log.read_text())
                    self.assertEqual(store.snapshot()['status'], 'finished')
                    self.assertIsNone(store.snapshot()['pending_alert'])
                    stop(process)
                    process = launch()
                    time.sleep(1.3)
                    self.assertIsNone(process.poll())
                    self.assertEqual(len(notification_log.read_text().splitlines()), 1)
                    self.assertEqual(len(sound_log.read_text().splitlines()), 1)
                finally:
                    if process is not None and process.poll() is None:
                        stop(process)
