"""Opt-in real Tk interaction tests, intended for an isolated virtual display."""

import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from chronocue.config import load_config, save_config
from chronocue.ui import ScheduleEditor


@unittest.skipUnless(os.environ.get('CHRONOCUE_TEST_GUI') == '1', 'Set CHRONOCUE_TEST_GUI=1 with a virtual display')
class WidgetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {'XDG_STATE_HOME': str(self.directory / 'state'), 'XDG_CACHE_HOME': str(self.directory / 'cache')})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.path = self.directory / 'schedule.json'
        save_config({'settings': {'sound_enabled': False}}, self.path)
        self.root = tk.Tk()
        self.root.withdraw()
        self.callback_errors = []
        self.root.report_callback_exception = lambda *args: self.callback_errors.append(args)
        self.editor = ScheduleEditor(self.root, self.path)
        self.addCleanup(self.editor.close)
        self.error_patch = patch('chronocue.ui.messagebox.showerror')
        self.errors = self.error_patch.start()
        self.addCleanup(self.error_patch.stop)
        self.root.update()

    def test_preset_and_schedule_controls_work_together(self):
        with patch('chronocue.ui.simpledialog.askstring', return_value='Work'):
            self.editor.preset_action('create')
        self.root.update()
        identity = self.editor.config['presets'][0]['id']
        self.assertEqual(self.editor.preset_var.get(), 'Work')
        self.editor.time_var.set('09:30')
        self.editor.title_var.set('Deep work')
        self.editor.entry_sound_var.set('Radar')
        self.editor.save_entry()
        self.root.update()
        entry = load_config(self.path)['schedules'][0]
        self.assertEqual((entry['preset_id'], entry['ringtone']), (identity, 'radar'))
        self.assertEqual(self.editor.tree.item(entry['id'], 'values')[0], '—')
        self.editor.preset_action('toggle')
        self.root.update()
        self.assertEqual(self.editor.tree.item(entry['id'], 'values')[0], '✓')
        self.editor.tree.selection_set(entry['id'])
        self.editor.bulk_preset_var.set('Ungrouped')
        self.editor.assign_selected()
        self.root.update()
        self.assertIsNone(load_config(self.path)['schedules'][0]['preset_id'])
        self.assertEqual(self.editor.tree.get_children(), ())
        self.assertFalse(self.callback_errors)
        self.errors.assert_not_called()

    def test_sound_and_pomodoro_settings_save_and_reload(self):
        self.editor.sound_tree.selection_set('homecoming')
        self.root.update()
        self.editor.sound_volume_var.set('65')
        self.editor.sound_enabled_var.set(True)
        self.editor.save_sound_settings()
        self.editor.pomodoro_vars['focus_minutes'].set('45')
        self.editor.pomodoro_vars['auto_start_breaks'].set(True)
        self.editor.timer_sound_var.set('Little Victory')
        self.editor.save_timer_settings()
        self.editor.reload_config()
        self.root.update()
        self.assertEqual(self.editor.config['settings']['ringtone'], 'homecoming')
        self.assertEqual(self.editor.config['settings']['volume'], 65)
        self.assertEqual(self.editor.config['pomodoro']['focus_minutes'], 45)
        self.assertEqual(self.editor.config['pomodoro']['ringtone'], 'little-victory')
        self.assertFalse(self.callback_errors)
        self.errors.assert_not_called()

    def test_timer_controls_and_audio_preview_are_responsive(self):
        with patch('chronocue.ui.daemon_is_running', return_value=True):
            self.editor.timer_command('start')
        self.editor.timer_command('pause')
        state = self.editor.pomodoro.snapshot(self.editor.config['pomodoro'])
        self.assertEqual(state['status'], 'paused')
        self.editor.timer_command('reset')
        self.assertEqual(self.editor.pomodoro.snapshot(self.editor.config['pomodoro'])['status'], 'idle')
        with patch('chronocue.ui.PLAYER.play') as play:
            self.editor.preview_sound()
            self.assertEqual(str(self.editor.preview_button['state']), 'disabled')
            play.call_args.args[2]((True, ''))
            self.editor.drain_results()
            self.assertEqual(str(self.editor.preview_button['state']), 'normal')
        self.assertFalse(self.callback_errors)
        self.errors.assert_not_called()

    def test_missing_daemon_does_not_start_unattended_timer(self):
        self.editor.timer_command('start')
        self.errors.assert_called_once()
        self.assertIn('service', self.errors.call_args.args[1])
        self.assertEqual(self.editor.pomodoro.snapshot(self.editor.config['pomodoro'])['status'], 'idle')

    def test_notification_lifetime_saves_and_preserves_custom_value(self):
        self.editor.notification_lifetime_var.set('Until dismissed')
        self.editor.save_sound_settings()
        self.assertEqual(load_config(self.path)['settings']['notification_timeout_ms'], 0)
        self.editor.notification_lifetime_var.set('30 seconds')
        self.editor.save_sound_settings()
        self.editor.reload_config()
        self.assertEqual(self.editor.notification_lifetime_var.get(), '30 seconds')
        config = load_config(self.path)
        config['settings']['notification_timeout_ms'] = 12345
        save_config(config, self.path)
        self.editor.reload_config()
        self.editor.save_sound_settings()
        self.assertEqual(load_config(self.path)['settings']['notification_timeout_ms'], 12345)
        self.errors.assert_not_called()

    def test_countdown_controls_and_independent_stopwatch(self):
        for key, value in (('hours', '0'), ('minutes', '0'), ('seconds', '5')):
            self.editor.duration_vars[key].set(value)
        self.editor.countdown_sound_var.set('Radar')
        with patch('chronocue.ui.daemon_is_running', return_value=True):
            self.editor.countdown_start.invoke()
        self.root.update()
        self.assertEqual(self.editor.countdown.snapshot()['status'], 'running')
        self.assertEqual(load_config(self.path)['countdown'], {'duration_seconds': 5, 'ringtone': 'radar'})
        self.assertEqual(str(self.editor.duration_inputs[0]['state']), 'disabled')
        self.editor.stopwatch_start.invoke()
        self.editor.stopwatch_lap.invoke()
        self.assertEqual(len(self.editor.lap_tree.get_children()), 1)
        self.editor.countdown_pause.invoke()
        self.assertEqual(self.editor.countdown.snapshot()['status'], 'paused')
        self.assertEqual(self.editor.stopwatch.snapshot()['status'], 'running')
        self.assertEqual(self.editor.countdown_start['text'], 'Resume')
        self.editor.stopwatch_pause.invoke()
        self.assertEqual(self.editor.stopwatch.snapshot()['status'], 'paused')
        self.editor.stopwatch_command('reset')
        self.assertEqual(self.editor.lap_tree.get_children(), ())
        self.errors.assert_not_called()
        self.assertFalse(self.callback_errors)

    def test_zero_countdown_and_missing_service_do_not_start(self):
        for variable in self.editor.duration_vars.values():
            variable.set('0')
        with patch('chronocue.ui.daemon_is_running', return_value=True):
            self.editor.countdown_command('start')
        self.assertEqual(self.editor.countdown.snapshot()['status'], 'idle')
        self.editor.duration_vars['seconds'].set('5')
        self.editor.countdown_command('start')
        self.assertEqual(self.editor.countdown.snapshot()['status'], 'idle')
        self.assertEqual(self.errors.call_count, 2)

    def test_second_window_shares_timer_stopwatch_and_laps(self):
        with patch('chronocue.ui.daemon_is_running', return_value=True):
            self.editor.countdown_command('start')
        self.editor.stopwatch_command('start')
        self.editor.stopwatch_command('lap')
        window = tk.Toplevel(self.root)
        window.withdraw()
        other = ScheduleEditor(window, self.path)
        try:
            self.assertEqual(other.single_status_var.get(), 'Counting down')
            self.assertEqual(other.stopwatch_status_var.get(), 'Running')
            self.assertEqual(len(other.lap_tree.get_children()), 1)
            other.stopwatch_command('pause')
            self.editor.refresh_clocks()
            self.assertEqual(self.editor.stopwatch_status_var.get(), 'Paused')
        finally:
            other.close()
        self.errors.assert_not_called()
