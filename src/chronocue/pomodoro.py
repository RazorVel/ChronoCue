"""Persistent Pomodoro state shared by the editor and notification daemon."""

from copy import deepcopy
import fcntl
import json
import math
import os
from pathlib import Path
import tempfile
import time
import uuid

from .state import state_path


PHASE_NAMES = {'focus': 'Focus', 'short_break': 'Short break', 'long_break': 'Long break'}


def phase_seconds(phase, options):
    return options[f'{phase}_minutes'] * 60


def initial_state(options):
    duration = phase_seconds('focus', options)
    return {
        'phase': 'focus', 'status': 'idle', 'deadline': None,
        'remaining_seconds': duration, 'duration_seconds': duration,
        'completed_focus': 0, 'pending_alert': None,
    }


def seconds_remaining(state, now=None):
    if state['status'] == 'running':
        return max(0, state['deadline'] - (time.time() if now is None else now))
    return state['remaining_seconds']


def _validate_state(data):
    if not isinstance(data, dict):
        raise ValueError('Pomodoro state must be an object')
    if not isinstance(data.get('phase'), str) or data['phase'] not in PHASE_NAMES or data.get('status') not in ('idle', 'ready', 'running', 'paused'):
        raise ValueError('Invalid Pomodoro phase or status')
    for field in ('duration_seconds', 'remaining_seconds'):
        value = data.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 14400 or not math.isfinite(value):
            raise ValueError(f'Invalid Pomodoro {field}')
    count = data.get('completed_focus')
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError('Invalid completed focus count')
    deadline = data.get('deadline')
    if data['status'] == 'running' and (
        isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not 0 <= deadline <= 1e12 or not math.isfinite(deadline)
    ):
        raise ValueError('Invalid Pomodoro deadline')
    event = data.get('pending_alert')
    if event is not None and (
        not isinstance(event, dict) or any(not isinstance(event.get(key), str) for key in ('id', 'title', 'message'))
    ):
        raise ValueError('Invalid Pomodoro alert')
    return data


def _advance(state, options, now, *, completed):
    previous = state['phase']
    if previous == 'focus':
        if completed:
            state['completed_focus'] += 1
        long_break = completed and state['completed_focus'] % options['long_break_every'] == 0
        phase = 'long_break' if long_break else 'short_break'
        auto_start = options['auto_start_breaks']
        title = 'Focus complete'
        message = f"Time for a {options[phase + '_minutes']}-minute {PHASE_NAMES[phase].lower()}."
    else:
        phase = 'focus'
        auto_start = options['auto_start_focus']
        title = 'Break complete'
        message = f"Ready for your next {options['focus_minutes']}-minute focus session."
    duration = phase_seconds(phase, options)
    state.update(
        phase=phase, status='running' if auto_start else 'ready',
        remaining_seconds=duration, duration_seconds=duration,
        deadline=now + duration if auto_start else None,
        pending_alert={'id': str(uuid.uuid4()), 'title': title, 'message': message} if completed else None,
    )


class PomodoroStore:
    def __init__(self, path):
        self.path = Path(path)

    @classmethod
    def for_config(cls, config_path):
        return cls(state_path(config_path, 'pomodoro'))

    def snapshot(self, options):
        try:
            return _validate_state(json.loads(self.path.read_text(encoding='utf-8')))
        except FileNotFoundError:
            return initial_state(options)

    def _write(self, state):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent, delete=False) as output:
                temporary = Path(output.name)
                json.dump(state, output, indent=2, allow_nan=False)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _update(self, options, action, *, reset=False):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(str(self.path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = initial_state(options) if reset else self.snapshot(options)
            before = deepcopy(state)
            action(state)
            _validate_state(state)
            if reset or state != before:
                self._write(state)
            return state

    def command(self, action, options, now=None):
        if action not in ('start', 'pause', 'reset', 'skip'):
            raise ValueError('Unknown Pomodoro command')
        now = time.time() if now is None else now

        def change(state):
            if action == 'reset':
                return
            if state['status'] == 'running' and seconds_remaining(state, now) == 0:
                _advance(state, options, now, completed=True)
                # A late click must not also pause/start/skip the next phase.
                return
            if action == 'start' and state['status'] != 'running':
                if state['status'] == 'idle':
                    state.update(remaining_seconds=phase_seconds('focus', options), duration_seconds=phase_seconds('focus', options))
                state['deadline'] = now + state['remaining_seconds']
                state['status'] = 'running'
            elif action == 'pause' and state['status'] == 'running':
                state['remaining_seconds'] = seconds_remaining(state, now)
                state.update(status='paused', deadline=None)
            elif action == 'skip':
                _advance(state, options, now, completed=False)

        return self._update(options, change, reset=action == 'reset')

    def tick(self, options, notify, now=None):
        now = time.time() if now is None else now
        snapshot = self.snapshot(options)
        if snapshot['status'] != 'running' and snapshot.get('pending_alert') is None:
            return snapshot

        def complete(state):
            if state['status'] == 'running' and seconds_remaining(state, now) == 0:
                # Start at most one fresh phase after suspend, without a catch-up storm.
                _advance(state, options, now, completed=True)

        state = self._update(options, complete)
        event = state.get('pending_alert')
        if event is not None and notify(event['title'], event['message']):
            def acknowledge(current):
                if current.get('pending_alert') and current['pending_alert']['id'] == event['id']:
                    current['pending_alert'] = None
            state = self._update(options, acknowledge)
        return state
