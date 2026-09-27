from array import array
import hashlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import wave

from chronocue import audio
from chronocue.config import validate_config


class AudioTest(unittest.TestCase):
    def test_at_least_thirty_distinct_valid_non_silent_ringtones(self):
        self.assertGreaterEqual(len(audio.RINGTONES), 30)
        digests = set()
        for ringtone in audio.RINGTONES:
            with self.subTest(ringtone=ringtone.id):
                data = audio.render_ringtone(ringtone.id)
                digests.add(hashlib.sha256(data).hexdigest())
                with wave.open(io.BytesIO(data), 'rb') as sound:
                    self.assertEqual((sound.getnchannels(), sound.getsampwidth(), sound.getframerate()), (1, 2, 22050))
                    self.assertGreater(sound.getnframes(), 5000)
                    samples = array('h', sound.readframes(sound.getnframes()))
                    self.assertGreater(max(abs(sample) for sample in samples), 1000)
                    self.assertLessEqual(max(abs(sample) for sample in samples), 24000)
        self.assertEqual(len(digests), len(audio.RINGTONES))

    def test_volume_zero_produces_silence(self):
        with wave.open(io.BytesIO(audio.render_ringtone('bright-bell', 0)), 'rb') as sound:
            self.assertEqual(set(sound.readframes(sound.getnframes())), {0})

    def test_cache_and_input_validation(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'XDG_CACHE_HOME': temporary}):
            first = audio.ringtone_file('radar', 70)
            with patch('chronocue.audio.render_ringtone') as render:
                self.assertEqual(audio.ringtone_file('radar', 70), first)
                render.assert_not_called()
            self.assertTrue(first.is_file())
            for identity, volume in (('../unsafe', 70), ('radar', '../unsafe'), ('radar', True), ('radar', 101)):
                with self.assertRaises(ValueError):
                    audio.ringtone_file(identity, volume)

    def test_missing_player_reports_actionable_error(self):
        with patch('chronocue.audio.available_players', return_value=[]):
            success, reason = audio.play_sound('radar')
        self.assertFalse(success)
        self.assertIn('Install', reason)

    def test_player_fallback_and_timeout(self):
        with patch('chronocue.audio.available_players', return_value=['/fake/paplay', '/fake/aplay']), patch('chronocue.audio.ringtone_file', return_value=Path('/fake/sound.wav')), patch('chronocue.audio.subprocess.run', side_effect=[subprocess.TimeoutExpired('paplay', 10), Mock(returncode=0)]) as run:
            self.assertTrue(audio.play_sound('radar')[0])
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args.kwargs['timeout'], 10)
            self.assertEqual(run.call_args.args[0], ['/fake/aplay', '/fake/sound.wav'])

    def test_worker_is_nonblocking_and_reports_result(self):
        entered, release, completed = threading.Event(), threading.Event(), threading.Event()
        results = []
        def play(*_args):
            entered.set()
            release.wait(2)
            return True, ''
        def callback(result):
            results.append(result)
            completed.set()
        with patch('chronocue.audio.play_sound', side_effect=play):
            player = audio.AudioPlayer()
            self.assertTrue(player.play('radar', 80, callback))
            self.assertTrue(entered.wait(2))
            self.assertFalse(completed.is_set())
            release.set()
            self.assertTrue(completed.wait(2))
        self.assertEqual(results, [(True, '')])

    def test_global_mute_and_silent_override_do_not_play(self):
        with patch.object(audio.PLAYER, 'play') as player:
            audio.play_configured_sound({'sound_enabled': False, 'ringtone': 'radar'})
            player.assert_not_called()
        with patch('chronocue.audio.play_sound') as play:
            self.assertTrue(audio.AudioPlayer().play('silent'))
            self.assertTrue(audio.AudioPlayer().play('radar', 0))
            play.assert_not_called()

    def test_invalid_sound_settings_are_rejected(self):
        for field, value in (('sound_enabled', 1), ('volume', -1), ('volume', 1.5), ('ringtone', 'missing')):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_config({'settings': {field: value}})
        with self.assertRaises(ValueError):
            validate_config({'schedules': [{'time': '13:00', 'ringtone': '../file'}]})
