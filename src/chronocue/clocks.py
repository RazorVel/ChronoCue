"""Persistent countdown and stopwatch clocks, shared safely between windows."""

from copy import deepcopy
import fcntl
import json
import math
import os
from pathlib import Path
import tempfile
import time
import uuid

from .config import DEFAULT_COUNTDOWN, validate_ringtone
from .state import state_path


def _number(value, maximum=1e12):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and 0 <= value <= maximum and math.isfinite(value))


def remaining_seconds(state, now=None):
    if state['status'] == 'running':
        return min(state['duration_seconds'], max(0, state['deadline'] - (time.time() if now is None else now)))
    return state['remaining_seconds']


def elapsed_seconds(state, now=None):
    elapsed = state['elapsed_seconds']
    if state['status'] == 'running':
        elapsed += max(0, (time.time() if now is None else now) - state['started_at'])
    return elapsed


class ClockStore:
    """Lock every mutation, replace atomically, and avoid writes on display ticks."""

    def __init__(self, path):
        self.path = Path(path)

    @classmethod
    def for_config(cls, config_path):
        return cls(state_path(config_path, cls.kind))

    def snapshot(self, options=None):
        try:
            return self.validate(json.loads(self.path.read_text(encoding='utf-8')))
        except FileNotFoundError:
            return self.initial(options)

    def update(self, action, options=None, *, reset=False):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(str(self.path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = self.initial(options) if reset else self.snapshot(options)
            before = deepcopy(state)
            action(state)
            self.validate(state)
            if reset or before != state:
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent, delete=False) as output:
                        temporary = Path(output.name)
                        json.dump(state, output, allow_nan=False)
                        output.write('\n')
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, self.path)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
            return state


class CountdownStore(ClockStore):
    kind = 'countdown'

    @staticmethod
    def initial(options=None):
        options = options or DEFAULT_COUNTDOWN
        return {'status': 'idle', 'duration_seconds': options['duration_seconds'],
                'remaining_seconds': options['duration_seconds'], 'deadline': None,
                'ringtone': options['ringtone'], 'pending_alert': None}

    @staticmethod
    def validate(state):
        if not isinstance(state, dict) or state.get('status') not in ('idle', 'running', 'paused', 'finished'):
            raise ValueError('Invalid countdown state')
        if not _number(state.get('duration_seconds'), 359999) or state['duration_seconds'] < 1:
            raise ValueError('Invalid countdown duration')
        if not _number(state.get('remaining_seconds'), state['duration_seconds']):
            raise ValueError('Invalid countdown remaining time')
        if state['status'] == 'running' and not _number(state.get('deadline')):
            raise ValueError('Invalid countdown deadline')
        validate_ringtone(state.get('ringtone'), 'Countdown sound')
        event = state.get('pending_alert')
        if event is not None and (not isinstance(event, dict) or any(
                not isinstance(event.get(key), str) for key in ('id', 'title', 'message'))):
            raise ValueError('Invalid countdown alert')
        return state

    @staticmethod
    def complete(state):
        state.update(status='finished', remaining_seconds=0, deadline=None,
                     pending_alert={'id': str(uuid.uuid4()), 'title': 'Timer complete',
                                    'message': 'Your countdown has finished.'})

    def command(self, action, options=None, now=None):
        if action not in ('start', 'pause', 'reset'):
            raise ValueError('Unknown countdown command')
        now = time.time() if now is None else now

        def change(state):
            if action == 'reset':
                return
            if state['status'] == 'running' and remaining_seconds(state, now) == 0:
                self.complete(state)
                return
            if action == 'start' and state['status'] != 'running':
                if state.get('pending_alert'):
                    raise ValueError('Completion alert is pending. Wait for delivery or Reset to clear it.')
                if state['status'] in ('idle', 'finished'):
                    state.update(self.initial(options))
                state.update(status='running', deadline=now + state['remaining_seconds'])
            elif action == 'pause' and state['status'] == 'running':
                state.update(remaining_seconds=remaining_seconds(state, now), status='paused', deadline=None)
        return self.update(change, options, reset=action == 'reset')

    def tick(self, options, notify, now=None):
        now = time.time() if now is None else now
        state = self.snapshot(options)
        if state['status'] == 'running' and remaining_seconds(state, now) == 0:
            def finish(current):
                if current['status'] == 'running' and remaining_seconds(current, now) == 0:
                    self.complete(current)
            state = self.update(finish, options)
        event = state.get('pending_alert')
        if event and notify(event['title'], event['message'], state.get('ringtone')):
            def acknowledge(current):
                if current.get('pending_alert') and current['pending_alert']['id'] == event['id']:
                    current['pending_alert'] = None
            state = self.update(acknowledge, options)
        return state


class StopwatchStore(ClockStore):
    kind = 'stopwatch'

    @staticmethod
    def initial(_options=None):
        return {'status': 'idle', 'elapsed_seconds': 0, 'started_at': None, 'laps': []}

    @staticmethod
    def validate(state):
        if not isinstance(state, dict) or state.get('status') not in ('idle', 'running', 'paused'):
            raise ValueError('Invalid stopwatch state')
        if not _number(state.get('elapsed_seconds')):
            raise ValueError('Invalid stopwatch elapsed time')
        if state['status'] == 'running' and not _number(state.get('started_at')):
            raise ValueError('Invalid stopwatch start time')
        laps = state.get('laps')
        if not isinstance(laps, list) or len(laps) > 100 or any(not _number(lap) for lap in laps):
            raise ValueError('Invalid stopwatch laps')
        if laps != sorted(laps):
            raise ValueError('Stopwatch laps must be ordered')
        return state

    def command(self, action, now=None):
        if action not in ('start', 'pause', 'lap', 'reset'):
            raise ValueError('Unknown stopwatch command')
        now = time.time() if now is None else now

        def change(state):
            if action == 'start' and state['status'] != 'running':
                state.update(status='running', started_at=now)
            elif action == 'pause' and state['status'] == 'running':
                elapsed = max(elapsed_seconds(state, now), max(state['laps'], default=0))
                state.update(elapsed_seconds=elapsed, status='paused', started_at=None)
            elif action == 'lap' and state['status'] == 'running':
                if len(state['laps']) >= 100:
                    raise ValueError('100 laps recorded. Reset to start a new session.')
                state['laps'].append(max(elapsed_seconds(state, now), max(state['laps'], default=0)))
        return self.update(change, reset=action == 'reset')
