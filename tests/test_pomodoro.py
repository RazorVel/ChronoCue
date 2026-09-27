from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from chronocue.config import DEFAULT_POMODORO, validate_config
from chronocue.pomodoro import PomodoroStore, seconds_remaining


class PomodoroTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'timer.json'
        self.store = PomodoroStore(self.path)
        self.options = {**DEFAULT_POMODORO, 'focus_minutes': 1, 'short_break_minutes': 1, 'long_break_minutes': 2, 'long_break_every': 2}

    def test_start_pause_resume_survives_reopening(self):
        state = self.store.command('start', self.options, now=100)
        self.assertEqual(seconds_remaining(state, now=115), 45)
        state = self.store.command('pause', self.options, now=120)
        self.assertEqual(seconds_remaining(state, now=500), 40)
        state = PomodoroStore(self.path).command('start', self.options, now=600)
        self.assertEqual(state['deadline'], 640)
        self.assertEqual(state['status'], 'running')

    def test_completion_waits_for_next_phase_and_deduplicates(self):
        self.store.command('start', self.options, now=100)
        notify = Mock(return_value=True)
        state = self.store.tick(self.options, notify, now=160)
        self.assertEqual((state['phase'], state['status'], state['completed_focus']), ('short_break', 'ready', 1))
        self.assertIsNone(state['pending_alert'])
        PomodoroStore(self.path).tick(self.options, notify, now=500)
        notify.assert_called_once()

    def test_long_break_after_configured_number_of_completed_focus_sessions(self):
        notify = Mock(return_value=True)
        self.store.command('start', self.options, now=0)
        self.store.tick(self.options, notify, now=60)
        self.store.command('start', self.options, now=60)
        self.store.tick(self.options, notify, now=120)
        self.store.command('start', self.options, now=120)
        state = self.store.tick(self.options, notify, now=180)
        self.assertEqual(state['phase'], 'long_break')
        self.assertEqual(state['remaining_seconds'], 120)
        self.assertEqual(state['completed_focus'], 2)

    def test_auto_start_and_suspend_advance_only_one_phase(self):
        options = {**self.options, 'auto_start_breaks': True, 'auto_start_focus': True}
        self.store.command('start', options, now=100)
        notify = Mock(return_value=True)
        state = self.store.tick(options, notify, now=10000)
        self.assertEqual(state['phase'], 'short_break')
        self.assertEqual(state['status'], 'running')
        self.assertEqual(state['deadline'], 10060)
        self.assertEqual(state['completed_focus'], 1)
        notify.assert_called_once()

    def test_skipped_focus_does_not_count_as_completed(self):
        self.store.command('start', self.options, now=0)
        state = self.store.command('skip', self.options, now=10)
        self.assertEqual(state['phase'], 'short_break')
        self.assertEqual(state['completed_focus'], 0)
        self.assertIsNone(state['pending_alert'])

    def test_failed_notification_retries_after_restart(self):
        self.store.command('start', self.options, now=0)
        notify = Mock(return_value=False)
        state = self.store.tick(self.options, notify, now=60)
        self.assertIsNotNone(state['pending_alert'])
        success = Mock(return_value=True)
        state = PomodoroStore(self.path).tick(self.options, success, now=70)
        self.assertIsNone(state['pending_alert'])
        self.assertEqual(success.call_count, 1)

    def test_reset_during_notification_acknowledgement_does_not_restore_old_state(self):
        self.store.command('start', self.options, now=0)
        def notify(*_args):
            self.store.command('reset', self.options, now=60)
            return True
        state = self.store.tick(self.options, notify, now=60)
        self.assertEqual(state['status'], 'idle')
        self.assertEqual(state['completed_focus'], 0)

    def test_late_pause_does_not_pause_the_following_phase(self):
        self.store.command('start', self.options, now=0)
        state = self.store.command('pause', self.options, now=70)
        self.assertEqual(state['phase'], 'short_break')
        self.assertEqual(state['status'], 'ready')
        self.assertIsNotNone(state['pending_alert'])

    def test_settings_changes_leave_running_deadline_unchanged(self):
        self.store.command('start', self.options, now=100)
        changed = {**self.options, 'focus_minutes': 10, 'short_break_minutes': 3}
        self.assertEqual(self.store.snapshot(changed)['deadline'], 160)
        state = self.store.tick(changed, Mock(return_value=True), now=160)
        self.assertEqual(state['remaining_seconds'], 180)

    def test_write_failure_preserves_old_state(self):
        self.store.command('start', self.options, now=100)
        original = self.path.read_bytes()
        with patch('chronocue.pomodoro.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.command('pause', self.options, now=110)
        self.assertEqual(self.path.read_bytes(), original)

    def test_corrupt_state_requires_explicit_reset(self):
        self.path.write_text('{broken')
        with self.assertRaises(ValueError):
            self.store.snapshot(self.options)
        state = self.store.command('reset', self.options)
        self.assertEqual(state['status'], 'idle')
        self.assertEqual(json.loads(self.path.read_text())['status'], 'idle')

    def test_invalid_options_are_rejected(self):
        for field, value in (('focus_minutes', 0), ('short_break_minutes', True), ('long_break_minutes', 241), ('long_break_every', 0), ('auto_start_breaks', 'yes'), ('ringtone', 'missing')):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_config({'pomodoro': {field: value}})
