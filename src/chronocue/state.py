"""Stable, per-configuration state locations."""

import fcntl
import hashlib
import os
from pathlib import Path


def state_path(config_path, kind=''):
    value = os.environ.get('XDG_STATE_HOME', '')
    root = Path(value) if value and Path(value).is_absolute() else Path.home() / '.local/state'
    identity = hashlib.sha256(str(Path(config_path).resolve()).encode('utf-8')).hexdigest()
    suffix = f'.{kind}' if kind else ''
    return root / 'chronocue' / f'{identity}{suffix}.json'


def daemon_is_running(config_path):
    path = state_path(config_path).with_suffix('.lock')
    try:
        with path.open('r') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lock, fcntl.LOCK_UN)
                return False
            except BlockingIOError:
                return True
    except FileNotFoundError:
        return False
