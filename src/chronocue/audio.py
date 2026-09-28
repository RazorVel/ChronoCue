"""Original, synthesized ringtones and bounded Linux audio playback."""

from array import array
from dataclasses import dataclass
import io
import logging
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import wave


@dataclass(frozen=True)
class Ringtone:
    id: str
    name: str
    category: str
    notes: tuple
    beat: float
    voice: str


# Original short motifs, synthesized locally; no downloads or licensed recordings.
RINGTONES = (
    Ringtone('bright-bell', 'Bright Bell', 'Bells', (84, 88, 91), .23, 'bell'),
    Ringtone('door-chime', 'Door Chime', 'Bells', (79, 72), .42, 'bell'),
    Ringtone('glass-drops', 'Glass Drops', 'Bells', (96, 91, 88, 84), .19, 'bell'),
    Ringtone('temple-bell', 'Temple Bell', 'Bells', (60, 0, 67), .40, 'bell'),
    Ringtone('silver-triangle', 'Silver Triangle', 'Bells', (93, 0, 93, 96), .20, 'bell'),
    Ringtone('morning-chimes', 'Morning Chimes', 'Bells', (76, 79, 83, 88), .24, 'bell'),
    Ringtone('tiny-glockenspiel', 'Tiny Glockenspiel', 'Bells', (84, 86, 91, 88), .17, 'bell'),
    Ringtone('deep-gong', 'Deep Gong', 'Bells', (43, 50), .58, 'bell'),
    Ringtone('double-ping', 'Double Ping', 'Digital', (88, 0, 88), .16, 'digital'),
    Ringtone('radar', 'Radar', 'Digital', (76, 0, 83, 0, 88), .14, 'digital'),
    Ringtone('arcade-rise', 'Arcade Rise', 'Digital', (60, 67, 72, 79, 84), .12, 'digital'),
    Ringtone('orbit', 'Orbit', 'Digital', (81, 88, 84, 76), .22, 'digital'),
    Ringtone('pixel-knock', 'Pixel Knock', 'Digital', (62, 62, 0, 69), .12, 'digital'),
    Ringtone('clear-signal', 'Clear Signal', 'Digital', (81, 0, 81, 0, 88), .22, 'digital'),
    Ringtone('space-call', 'Space Call', 'Digital', (72, 84, 79, 91), .18, 'digital'),
    Ringtone('triple-alert', 'Triple Alert', 'Digital', (86, 0, 86, 0, 86), .15, 'digital'),
    Ringtone('soft-marimba', 'Soft Marimba', 'Soft', (67, 72, 76), .28, 'pluck'),
    Ringtone('bamboo', 'Bamboo', 'Soft', (69, 74, 69, 81), .22, 'pluck'),
    Ringtone('warm-piano', 'Warm Piano', 'Soft', (60, 64, 67, 72), .26, 'soft'),
    Ringtone('gentle-wave', 'Gentle Wave', 'Soft', (72, 76, 74, 69), .30, 'soft'),
    Ringtone('quiet-fifth', 'Quiet Fifth', 'Soft', (65, 72), .42, 'soft'),
    Ringtone('raindrop', 'Raindrop', 'Soft', (88, 79, 83, 74), .18, 'pluck'),
    Ringtone('wood-blocks', 'Wood Blocks', 'Soft', (57, 64, 60, 67), .14, 'pluck'),
    Ringtone('evening-glow', 'Evening Glow', 'Soft', (64, 71, 76, 68), .32, 'soft'),
    Ringtone('fresh-start', 'Fresh Start', 'Melodies', (72, 76, 79, 84, 79), .19, 'pluck'),
    Ringtone('little-victory', 'Little Victory', 'Melodies', (67, 72, 76, 79, 84), .18, 'bell'),
    Ringtone('take-a-breath', 'Take a Breath', 'Melodies', (81, 76, 72, 69), .33, 'soft'),
    Ringtone('time-to-focus', 'Time to Focus', 'Melodies', (60, 67, 64, 72), .25, 'digital'),
    Ringtone('sunrise', 'Sunrise', 'Melodies', (62, 69, 74, 78, 81), .25, 'soft'),
    Ringtone('sparkle', 'Sparkle', 'Melodies', (88, 91, 96, 100, 96), .14, 'bell'),
    Ringtone('rolling-stones', 'Rolling Stones', 'Melodies', (72, 67, 69, 64, 60), .20, 'pluck'),
    Ringtone('homecoming', 'Homecoming', 'Melodies', (76, 79, 74, 71, 72), .24, 'soft'),
)
RINGTONE_BY_ID = {ringtone.id: ringtone for ringtone in RINGTONES}
DEFAULT_RINGTONE = 'bright-bell'
SAMPLE_RATE = 22050


def render_ringtone(ringtone_id, volume=80):
    """Return a mono PCM WAV, with volume baked in for every playback backend."""
    if ringtone_id not in RINGTONE_BY_ID:
        raise ValueError('Choose a built-in ringtone.')
    if isinstance(volume, bool) or not isinstance(volume, int) or not 0 <= volume <= 100:
        raise ValueError('Volume must be an integer from 0 to 100.')
    ringtone = RINGTONE_BY_ID[ringtone_id]
    samples = array('h')
    for index, note in enumerate(ringtone.notes):
        duration = ringtone.beat * (2.1 if index == len(ringtone.notes) - 1 else 1)
        count = int(SAMPLE_RATE * duration)
        frequency = 440 * 2 ** ((note - 69) / 12) if note else 0
        for frame in range(count):
            if not note:
                samples.append(0)
                continue
            t = frame / SAMPLE_RATE
            phase = 2 * math.pi * frequency * t
            attack = min(1, t / (.045 if ringtone.voice == 'soft' else .008))
            release = min(1, (duration - t) / .045)
            if ringtone.voice == 'bell':
                value = (.73 * math.sin(phase) + .17 * math.sin(phase * 2.76)
                         + .10 * math.sin(phase * 4.07)) * math.exp(-3.8 * t)
            elif ringtone.voice == 'digital':
                value = (.8 * math.sin(phase) + .2 * math.sin(phase * 3))
            elif ringtone.voice == 'pluck':
                value = (.8 * math.sin(phase) + .2 * math.sin(phase * 2)) * math.exp(-7 * t)
            else:
                value = (.9 * math.sin(phase) + .1 * math.sin(phase * 2)) * math.exp(-1.6 * t)
            samples.append(round(24000 * volume / 100 * attack * release * value))
    samples.extend([0] * (SAMPLE_RATE // 10))
    if sys.byteorder != 'little':
        samples.byteswap()
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(samples.tobytes())
    return buffer.getvalue()


def ringtone_file(ringtone_id, volume):
    # Validate before using the ID in a filename.
    if ringtone_id not in RINGTONE_BY_ID:
        raise ValueError('Unknown ringtone')
    if isinstance(volume, bool) or not isinstance(volume, int) or not 0 <= volume <= 100:
        raise ValueError('Volume must be an integer from 0 to 100.')
    cache = os.environ.get('XDG_CACHE_HOME', '')
    root = Path(cache) if cache and Path(cache).is_absolute() else Path.home() / '.cache'
    directory = root / 'chronocue' / 'sounds-v1'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{ringtone_id}-{volume}.wav'
    if path.is_file():
        return path
    data = render_ringtone(ringtone_id, volume)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def available_players():
    return [path for name in ('paplay', 'pw-play', 'aplay') if (path := shutil.which(name))]


def play_sound(ringtone_id, volume=80):
    """Play synchronously; callers in the editor use AudioPlayer's worker."""
    players = available_players()
    if not players:
        return False, 'No audio player found. Install pulseaudio-utils, pipewire-bin, or alsa-utils.'
    try:
        path = ringtone_file(ringtone_id, volume)
        with wave.open(str(path), 'rb') as sound:
            # Bound a failed backend by this short cue's length, not ten seconds.
            timeout = sound.getnframes() / sound.getframerate() + 1
        for binary in players:
            try:
                options = {
                    'paplay': ['--latency-msec=50'],
                    'pw-play': ['--latency=50ms'],
                    'aplay': ['--buffer-time=50000'],
                }.get(Path(binary).name, [])
                result = subprocess.run(
                    [binary, *options, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False, timeout=timeout,
                )
                if result.returncode == 0:
                    return True, ''
            except (OSError, subprocess.TimeoutExpired):
                continue
    except (OSError, ValueError, wave.Error, EOFError) as exc:
        return False, str(exc)
    return False, 'Audio playback failed. Check your audio output and desktop audio service.'


class AudioPlayer:
    """Start short cues immediately; never queue a stale alarm behind other sounds."""

    def __init__(self):
        self.slots = threading.BoundedSemaphore(4)

    def play(self, ringtone_id, volume=80, callback=None):
        if ringtone_id == 'silent' or volume == 0:
            if callback:
                callback((True, ''))
            return True
        if not self.slots.acquire(blocking=False):
            logging.warning('Alert audio: four sounds are already playing')
            if callback:
                callback((False, 'Audio is busy. Try again in a moment.'))
            return False
        thread = threading.Thread(target=self._work, args=(ringtone_id, volume, callback),
                                  daemon=True, name='chronocue-audio')
        try:
            thread.start()
        except RuntimeError:
            self.slots.release()
            if callback:
                callback((False, 'Could not start audio playback. Try again.'))
            return False
        return True

    def _work(self, ringtone_id, volume, callback):
        try:
            try:
                result = play_sound(ringtone_id, volume)
            except Exception:
                logging.exception('Could not play alert audio')
                result = False, 'Could not play alert audio.'
            if not result[0]:
                logging.warning('Alert audio: %s', result[1])
            if callback:
                callback(result)
        finally:
            self.slots.release()


PLAYER = AudioPlayer()


def play_configured_sound(settings, override=None):
    if settings.get('sound_enabled', False):
        return PLAYER.play(override or settings.get('ringtone', DEFAULT_RINGTONE), settings.get('volume', 80))
    return False
