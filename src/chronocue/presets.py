"""Schedule group operations shared by the editor and daemon."""

from copy import deepcopy
import uuid

from .config import validate_config


def schedule_is_active(entry, presets):
    if not entry.get('enabled', True):
        return False
    preset_id = entry.get('preset_id')
    if preset_id is None:
        return True
    return any(preset['id'] == preset_id and preset.get('enabled', False) for preset in presets)


def find_preset(config, preset_id):
    for preset in config['presets']:
        if preset['id'] == preset_id:
            return preset
    raise ValueError('Select an existing preset.')


def create_preset(config, name):
    candidate = deepcopy(config)
    preset = {'id': str(uuid.uuid4()), 'name': name, 'enabled': False}
    candidate['presets'].append(preset)
    return validate_config(candidate), preset['id']


def rename_preset(config, preset_id, name):
    candidate = deepcopy(config)
    find_preset(candidate, preset_id)['name'] = name
    return validate_config(candidate)


def toggle_preset(config, preset_id):
    candidate = deepcopy(config)
    preset = find_preset(candidate, preset_id)
    preset['enabled'] = not preset['enabled']
    return validate_config(candidate)


def duplicate_preset(config, preset_id, name):
    original = find_preset(config, preset_id)
    candidate, new_id = create_preset(config, name or original['name'] + ' copy')
    for entry in config['schedules']:
        if entry.get('preset_id') == preset_id:
            duplicate = {**deepcopy(entry), 'id': str(uuid.uuid4()), 'preset_id': new_id}
            candidate['schedules'].append(duplicate)
    return validate_config(candidate), new_id


def delete_preset(config, preset_id):
    find_preset(config, preset_id)
    candidate = deepcopy(config)
    candidate['presets'] = [item for item in candidate['presets'] if item['id'] != preset_id]
    candidate['schedules'] = [item for item in candidate['schedules'] if item.get('preset_id') != preset_id]
    return validate_config(candidate)


def assign_schedules(config, entry_ids, preset_id):
    if preset_id is not None:
        find_preset(config, preset_id)
    if not entry_ids:
        raise ValueError('Select one or more schedules first.')
    candidate = deepcopy(config)
    found = set()
    for entry in candidate['schedules']:
        if entry['id'] in entry_ids:
            entry['preset_id'] = preset_id
            found.add(entry['id'])
    if found != set(entry_ids):
        raise ValueError('A selected schedule no longer exists. Reload and try again.')
    return validate_config(candidate)
