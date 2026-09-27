from copy import deepcopy
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chronocue.config import validate_config
from chronocue.daemon import DeliveryHistory, deliver_due
from chronocue import presets


class PresetTest(unittest.TestCase):
    def setUp(self):
        self.config = validate_config({'schedules': [{'id': 'one', 'time': '13:00', 'title': 'One'}]})

    def test_create_assign_toggle_and_ungrouped_compatibility(self):
        self.assertTrue(presets.schedule_is_active(self.config['schedules'][0], []))
        config, identity = presets.create_preset(self.config, 'Work')
        config = presets.assign_schedules(config, ['one'], identity)
        self.assertFalse(presets.schedule_is_active(config['schedules'][0], config['presets']))
        config = presets.toggle_preset(config, identity)
        self.assertTrue(presets.schedule_is_active(config['schedules'][0], config['presets']))
        config['schedules'][0]['enabled'] = False
        self.assertFalse(presets.schedule_is_active(config['schedules'][0], config['presets']))
        self.assertNotIn('preset_id', self.config['schedules'][0])

    def test_multiple_presets_are_independent(self):
        config, first = presets.create_preset(self.config, 'Work')
        config, second = presets.create_preset(config, 'Exercise')
        config = presets.toggle_preset(presets.toggle_preset(config, first), second)
        self.assertTrue(all(item['enabled'] for item in config['presets']))
        config = presets.toggle_preset(config, first)
        self.assertFalse(presets.find_preset(config, first)['enabled'])
        self.assertTrue(presets.find_preset(config, second)['enabled'])

    def test_duplicate_uses_new_ids_and_starts_inactive(self):
        config, identity = presets.create_preset(self.config, 'Work')
        config = presets.toggle_preset(presets.assign_schedules(config, ['one'], identity), identity)
        copied, duplicate = presets.duplicate_preset(config, identity, 'Weekend work')
        self.assertFalse(presets.find_preset(copied, duplicate)['enabled'])
        clone = next(item for item in copied['schedules'] if item['preset_id'] == duplicate)
        self.assertNotEqual(clone['id'], 'one')
        self.assertEqual(clone['title'], 'One')
        self.assertEqual(len(config['schedules']), 1)

    def test_rename_and_delete_do_not_affect_other_groups(self):
        config, identity = presets.create_preset(self.config, 'Work')
        config = presets.assign_schedules(config, ['one'], identity)
        config['schedules'].append({'id': 'two', 'time': '14:00', 'title': 'Keep'})
        config = presets.rename_preset(config, identity, 'Office')
        self.assertEqual(presets.find_preset(config, identity)['name'], 'Office')
        result = presets.delete_preset(config, identity)
        self.assertEqual([item['id'] for item in result['schedules']], ['two'])
        self.assertEqual(result['presets'], [])

    def test_invalid_presets_and_references_are_rejected(self):
        config, identity = presets.create_preset(self.config, 'Work')
        cases = [
            {'presets': {}}, {'presets': [None]},
            {'presets': [{'id': 'x', 'name': ' '}]},
            {'presets': [{'id': 'x', 'name': 'All schedules'}]},
            {'presets': [{'id': 'x', 'name': 'Work', 'enabled': 'yes'}]},
            {'presets': [{'id': 'x', 'name': 'Work'}, {'id': 'y', 'name': 'work'}]},
            {'schedules': [{'time': '12:00', 'preset_id': 'missing'}]},
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                validate_config(case)
        with self.assertRaises(ValueError):
            presets.assign_schedules(config, ['missing'], identity)
        with self.assertRaises(ValueError):
            presets.assign_schedules(config, [], identity)

    def test_activation_delivers_due_schedules_once_without_losing_history(self):
        config, identity = presets.create_preset(self.config, 'Work')
        config = presets.assign_schedules(config, ['one'], identity)
        config['settings']['sound_enabled'] = False
        with tempfile.TemporaryDirectory() as temporary:
            history = DeliveryHistory(Path(temporary) / 'history.json')
            with patch('chronocue.daemon.send_notification', return_value=True) as notify:
                now = datetime(2026, 9, 28, 13)
                deliver_due(config, now, history)
                notify.assert_not_called()
                config = presets.toggle_preset(config, identity)
                deliver_due(config, now, history)
                config = presets.toggle_preset(presets.toggle_preset(config, identity), identity)
                deliver_due(config, now, history)
                notify.assert_called_once()

    def test_schedule_audio_override_is_used_only_after_successful_notification(self):
        config = deepcopy(self.config)
        config['schedules'][0]['ringtone'] = 'radar'
        with tempfile.TemporaryDirectory() as temporary:
            history = DeliveryHistory(Path(temporary) / 'history.json')
            with patch('chronocue.daemon.send_notification', side_effect=[False, True]), patch('chronocue.daemon.play_configured_sound') as sound:
                with self.assertLogs(level='ERROR'):
                    deliver_due(config, datetime(2026, 9, 28, 13), history)
                sound.assert_not_called()
                deliver_due(config, datetime(2026, 9, 28, 13), history)
                sound.assert_called_once_with(config['settings'], 'radar')
