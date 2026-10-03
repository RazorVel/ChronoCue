import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chronocue.config import (
    ConfigConflictError, DAY_NAMES, DEFAULT_CONFIG, config_revision,
    default_config_path, load_config, load_config_snapshot, parse_clock,
    resolve_config_path, save_config, validate_config,
)


class ValidationTest(unittest.TestCase):
    def test_defaults_do_not_share_mutable_values(self):
        result = validate_config({})
        result['settings']['poll_seconds'] = 17
        result['schedules'].append({})
        self.assertEqual(DEFAULT_CONFIG['settings']['poll_seconds'], 5)
        self.assertEqual(DEFAULT_CONFIG['schedules'], [])

    def test_invalid_settings_are_rejected(self):
        cases = {
            'poll_seconds': [True, 0, -1, '5', None, float('nan'), float('inf'), 86401],
            'max_late_seconds': [True, -1, 1.5, '120', None, 86401],
            'notification_timeout_ms': [True, -2, 1.5, '10', None, 2147483648],
            'urgency': ['', 'urgent', None, [], 1],
        }
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_config({'settings': {field: value}})

    def test_clock_requires_strict_24_hour_format(self):
        for value in ('1:00', '01:0', '24:00', '12:60', ' 12:00', '12:00:00', None, 1200):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_clock(value)
        self.assertEqual(parse_clock('00:00'), (0, 0))
        self.assertEqual(parse_clock('23:59'), (23, 59))

    def test_invalid_structures_and_entries_are_rejected(self):
        cases = [[], {'settings': None}, {'schedules': {}}, {'schedules': [None]}]
        for fields in ({}, {'time': None}, {'time': '1:00'}, {'days': 'mon'},
                       {'days': [1]}, {'days': ['holiday']}, {'enabled': 'false'},
                       {'message': 1}, {'message': 'bad\0text'}, {'title': 'bad\0text'}, {'title': ' '}, {'id': ''}, {'id': 1}):
            entry = {'time': '13:00', **fields} if fields else {}
            cases.append({'schedules': [entry]})
        cases.append({'schedules': [{'time': '13:00', 'id': 'same'}, {'time': '14:00', 'id': 'same'}]})
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_config(data)

    def test_weekdays_normalize_and_empty_preserves_daily_semantics(self):
        for days in (None, []):
            entry = validate_config({'schedules': [{'time': '13:00', 'days': days}]})['schedules'][0]
            self.assertEqual(entry['days'], list(DAY_NAMES))
        entry = validate_config({'schedules': [{'time': '13:00', 'days': [' SUN ', 'mon', 'sun']}]})['schedules'][0]
        self.assertEqual(entry['days'], ['mon', 'sun'])

    def test_legacy_ids_are_stable_unique_and_survive_reordering(self):
        a, b = {'time': '13:00'}, {'time': '14:00'}
        forward = validate_config({'schedules': [a, b, a]})['schedules']
        reverse = validate_config({'schedules': [b, a, a]})['schedules']
        self.assertEqual(len({entry['id'] for entry in forward}), 3)
        self.assertEqual(forward[0]['id'], reverse[1]['id'])
        self.assertEqual(forward[1]['id'], reverse[0]['id'])
        self.assertNotIn('id', a)

    def test_unknown_fields_and_zero_grace_are_preserved(self):
        source = {'note': 'keep', 'settings': {'future': 42, 'max_late_seconds': 0},
                  'schedules': [{'time': '13:00', 'extra': {'tag': 'work'}}]}
        result = validate_config(source)
        self.assertEqual(result['note'], 'keep')
        self.assertEqual(result['settings']['future'], 42)
        self.assertEqual(result['settings']['max_late_seconds'], 0)
        self.assertEqual(result['schedules'][0]['extra'], {'tag': 'work'})


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'config' / 'schedule.json'

    def test_first_load_creates_valid_private_config(self):
        self.assertEqual(load_config(self.path), DEFAULT_CONFIG)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_snapshot_revision_matches_bytes_and_load_does_not_rewrite(self):
        self.path.parent.mkdir()
        content = b'{"schedules": [{"time": "13:00"}]}\n'
        self.path.write_bytes(content)
        data, revision = load_config_snapshot(self.path)
        self.assertEqual(revision, config_revision(self.path))
        self.assertTrue(data['schedules'][0]['id'])
        self.assertEqual(self.path.read_bytes(), content)

    def test_save_returns_revision_and_preserves_mode(self):
        data = load_config(self.path)
        self.path.chmod(0o640)
        revision = save_config(data, self.path)
        self.assertEqual(revision, config_revision(self.path))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertTrue(self.path.read_bytes().endswith(b'\n'))

    def test_stale_save_keeps_external_edit(self):
        data, revision = load_config_snapshot(self.path)
        external = copy.deepcopy(data)
        external['settings']['poll_seconds'] = 10
        save_config(external, self.path)
        before = self.path.read_bytes()
        with self.assertRaises(ConfigConflictError):
            save_config(data, self.path, expected_revision=revision)
        self.assertEqual(self.path.read_bytes(), before)

    def test_concurrent_editors_cannot_both_save_the_same_revision(self):
        data, revision = load_config_snapshot(self.path)

        def attempt(value):
            candidate = copy.deepcopy(data)
            candidate['settings']['poll_seconds'] = value
            try:
                save_config(candidate, self.path, expected_revision=revision)
                return value
            except ConfigConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as writers:
            results = list(writers.map(attempt, (7, 9)))
        winners = [value for value in results if value is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(load_config(self.path)['settings']['poll_seconds'], winners[0])

    def test_create_only_does_not_overwrite_existing_file(self):
        load_config(self.path)
        before = self.path.read_bytes()
        with self.assertRaises(ConfigConflictError):
            save_config(DEFAULT_CONFIG, self.path, expected_revision=None)
        self.assertEqual(self.path.read_bytes(), before)

    def test_failed_replace_leaves_old_file_and_cleans_temporary(self):
        data = load_config(self.path)
        before = self.path.read_bytes()
        with patch('chronocue.config.os.replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                save_config(data, self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])
        self.assertEqual(list(self.path.parent.glob('.*.tmp')), [])

    def test_invalid_save_never_replaces_file(self):
        load_config(self.path)
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            save_config({'settings': {'poll_seconds': 'bad'}}, self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_json_is_not_replaced(self):
        self.path.parent.mkdir()
        self.path.write_text('{broken')
        with self.assertRaises(json.JSONDecodeError):
            load_config(self.path)
        self.assertEqual(self.path.read_text(), '{broken')

    def test_xdg_defaults_and_path_precedence(self):
        with patch.dict(os.environ, {'HOME': self.temp.name, 'XDG_CONFIG_HOME': '', 'CHRONOCUE_CONFIG': ''}):
            self.assertEqual(default_config_path(), Path(self.temp.name) / '.config/chronocue/config.json')
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': 'relative'}):
                self.assertEqual(default_config_path(), Path(self.temp.name) / '.config/chronocue/config.json')
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.path.parent), 'CHRONOCUE_CONFIG': '~/custom.json'}):
                self.assertEqual(resolve_config_path(), Path(self.temp.name) / 'custom.json')
                self.assertEqual(resolve_config_path(self.path), self.path)


if __name__ == '__main__':
    unittest.main()
