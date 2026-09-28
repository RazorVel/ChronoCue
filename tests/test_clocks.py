import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from chronocue.clocks import CountdownStore, StopwatchStore, elapsed_seconds, remaining_seconds
from chronocue.config import DEFAULT_COUNTDOWN, validate_config


class ClocksTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'clock.json'
        self.timer = CountdownStore(self.path)
        self.options = {'duration_seconds': 65, 'ringtone': 'radar'}
        self.watch = StopwatchStore(self.path.with_name('stopwatch.json'))

    def test_legacy_config_uses_persistent_alert_and_countdown_defaults(self):
        config = validate_config({})
        self.assertEqual(config['settings']['notification_timeout_ms'], 0)
        self.assertEqual(config['countdown'], DEFAULT_COUNTDOWN)
        self.assertEqual(validate_config({'settings': {'notification_timeout_ms': 1234}})['settings']['notification_timeout_ms'], 1234)

    def test_invalid_durations_and_sounds_rejected(self):
        for value in (0, -1, 360000, True, 2.5, '3', float('nan')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_config({'countdown': {'duration_seconds': value}})
        with self.assertRaises(ValueError):
            validate_config({'countdown': {'ringtone': 'unknown'}})
        self.assertEqual(validate_config({'countdown': {'duration_seconds': 359999}})['countdown']['duration_seconds'], 359999)

    def test_countdown_pause_resume_and_reopen(self):
        self.timer.command('start', self.options, now=100)
        self.assertEqual(remaining_seconds(CountdownStore(self.path).snapshot(), now=110), 55)
        self.timer.command('pause', self.options, now=110)
        self.assertEqual(remaining_seconds(self.timer.snapshot(), now=999), 55)
        self.timer.command('start', self.options, now=1000)
        self.assertEqual(remaining_seconds(self.timer.snapshot(), now=1010), 45)
        self.timer.command('reset', self.options)
        self.assertEqual(self.timer.snapshot()['status'], 'idle')

    def test_overdue_countdown_completes_once_after_downtime(self):
        self.timer.command('start', self.options, now=100)
        notify = Mock(return_value=True)
        self.timer.tick(self.options, notify, now=10000)
        self.timer.tick(self.options, notify, now=20000)
        notify.assert_called_once_with('Timer complete', 'Your countdown has finished.', 'radar')
        self.assertEqual(self.timer.snapshot()['status'], 'finished')
        self.assertIsNone(self.timer.snapshot()['pending_alert'])
        self.timer.command('start', self.options, now=20000)
        self.assertEqual(remaining_seconds(self.timer.snapshot(), now=20000), 65)

    def test_pending_notification_retried_after_reopening(self):
        self.timer.command('start', self.options, now=100)
        self.timer.tick(self.options, Mock(return_value=False), now=165)
        with self.assertRaises(ValueError):
            self.timer.command('start', self.options, now=200)
        store = CountdownStore(self.path)
        notify = Mock(return_value=True)
        store.tick(self.options, notify, now=200)
        notify.assert_called_once()
        self.assertIsNone(store.snapshot()['pending_alert'])

    def test_late_pause_preserves_completion(self):
        self.timer.command('start', self.options, now=100)
        state = self.timer.command('pause', self.options, now=165)
        self.assertEqual(state['status'], 'finished')
        self.assertIsNotNone(state['pending_alert'])

    def test_reset_during_notification_does_not_overwrite_new_timer(self):
        self.timer.command('start', self.options, now=100)
        def notify(*_):
            self.timer.command('reset', self.options)
            self.timer.command('start', self.options, now=200)
            return True
        self.timer.tick(self.options, notify, now=165)
        self.assertEqual(self.timer.snapshot()['deadline'], 265)

    def test_running_countdown_keeps_duration_and_selected_sound(self):
        self.timer.command('start', self.options, now=100)
        newer = {'duration_seconds': 10, 'ringtone': 'homecoming'}
        self.timer.command('start', newer, now=105)
        notify = Mock(return_value=True)
        self.timer.tick(newer, notify, now=165)
        self.assertEqual(notify.call_args.args[2], 'radar')
        self.timer.command('start', newer, now=200)
        self.assertEqual(self.timer.snapshot()['deadline'], 210)

    def test_running_display_and_tick_do_not_write_repeatedly(self):
        self.timer.command('start', self.options, now=100)
        before = self.path.stat().st_mtime_ns
        self.timer.tick(self.options, Mock(), now=110)
        self.timer.snapshot(self.options)
        self.assertEqual(self.path.stat().st_mtime_ns, before)

    def test_stopwatch_laps_pause_resume_and_reopen(self):
        self.watch.command('start', now=100)
        self.watch.command('lap', now=112.25)
        self.watch.command('pause', now=120)
        self.assertEqual(elapsed_seconds(self.watch.snapshot(), now=500), 20)
        other = StopwatchStore(self.watch.path)
        other.command('start', now=600)
        other.command('lap', now=603.5)
        self.assertEqual(other.snapshot()['laps'], [12.25, 23.5])
        self.assertEqual(elapsed_seconds(other.snapshot(), now=610), 30)
        other.command('reset')
        self.assertEqual(other.snapshot(), other.initial())

    def test_stopwatch_concurrent_laps_are_not_lost(self):
        self.watch.command('start', now=100)
        errors = []
        def lap():
            try:
                StopwatchStore(self.watch.path).command('lap', now=110)
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=lap) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        self.assertEqual(self.watch.snapshot()['laps'], [10] * 12)

    def test_stopwatch_lap_limit_and_paused_lap(self):
        self.watch.command('lap', now=1)
        self.assertFalse(self.watch.path.exists())
        self.watch.command('start', now=100)
        for n in range(100):
            self.watch.command('lap', now=101 + n)
        with self.assertRaises(ValueError):
            self.watch.command('lap', now=300)
        self.watch.command('pause', now=300)
        self.watch.command('lap', now=400)
        self.assertEqual(len(self.watch.snapshot()['laps']), 100)

    def test_corrupt_state_is_reported_and_reset_recovers(self):
        for store in (self.timer, self.watch):
            store.path.write_text('{bad')
            with self.assertRaises(ValueError):
                store.snapshot()
            store.command('reset')
            self.assertEqual(store.snapshot()['status'], 'idle')
        for invalid in (None, [], {'status': 'running'}, {'status': 'idle', 'elapsed_seconds': float('nan')}):
            self.watch.path.write_text(json.dumps(invalid))
            with self.assertRaises(ValueError):
                self.watch.snapshot()

    def test_invalid_mutation_leaves_saved_state_intact(self):
        self.timer.command('start', self.options, now=100)
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.timer.update(lambda state: state.update(remaining_seconds=float('inf')))
        self.assertEqual(self.path.read_bytes(), before)

    def test_backward_clock_adjustment_does_not_break_pause(self):
        self.timer.command('start', self.options, now=100)
        state = self.timer.command('pause', self.options, now=90)
        self.assertEqual(state['status'], 'paused')
        self.assertEqual(state['remaining_seconds'], 65)
