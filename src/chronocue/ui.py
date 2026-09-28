import argparse
from copy import deepcopy
import math
import queue
import threading
import uuid
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .audio import DEFAULT_RINGTONE, PLAYER, RINGTONES, RINGTONE_BY_ID
from .config import (
    ConfigConflictError, DAY_NAMES, load_config_snapshot, parse_clock,
    resolve_config_path, save_config, validate_config,
)
from .notifier import send_notification
from .clocks import CountdownStore, StopwatchStore, elapsed_seconds, remaining_seconds
from .pomodoro import PHASE_NAMES, PomodoroStore, seconds_remaining
from . import presets
from .state import daemon_is_running


SOUND_LABELS = {'Use default': None, 'Silent': 'silent', **{item.name: item.id for item in RINGTONES}}
NOTIFICATION_LIFETIMES = {'Until dismissed': 0, 'Desktop default': -1, '10 seconds': 10000,
                          '30 seconds': 30000, '1 minute': 60000, '5 minutes': 300000}


def format_duration(seconds, *, fractions=False):
    total = max(0, int(seconds * 100) if fractions else math.ceil(seconds))
    whole = total // 100 if fractions else total
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f'{hours:02d}:{minutes:02d}:{secs:02d}' + (f'.{total % 100:02d}' if fractions else '')


class ScheduleEditor:
    def __init__(self, root, config_path):
        self.root = root
        self.config_path = config_path
        self.config, self.config_revision = load_config_snapshot(config_path)
        self.selected_id = None
        self.filter_id = 'all'
        self.pomodoro = PomodoroStore.for_config(config_path)
        self.countdown = CountdownStore.for_config(config_path)
        self.stopwatch = StopwatchStore.for_config(config_path)
        self.results = queue.Queue()
        self.after_ids = {}
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.title('ChronoCue')
        root.geometry('1160x800')
        root.minsize(1040, 720)
        self.build_ui()
        self.refresh_presets()
        self.refresh_tree()
        self.load_preferences()
        self.timer_tick()
        self.drain_results()

    def build_ui(self):
        style = ttk.Style(self.root)
        style.configure('Title.TLabel', font=('TkDefaultFont', 21, 'bold'))
        style.configure('Heading.TLabel', font=('TkDefaultFont', 12, 'bold'))
        style.configure('Timer.TLabel', font=('TkDefaultFont', 62, 'bold'))
        style.configure('Treeview', rowheight=27)
        shell = ttk.Frame(self.root, padding=18)
        shell.pack(fill='both', expand=True)
        header = ttk.Frame(shell)
        header.pack(fill='x', pady=(0, 14))
        ttk.Label(header, text='ChronoCue', style='Title.TLabel').pack(side='left')
        ttk.Label(header, text='Schedules, focus sessions, and audible reminders.').pack(side='left', padx=22)
        ttk.Button(header, text='Reload', command=self.reload_config).pack(side='right')
        self.status_var = tk.StringVar(value='Ready')
        ttk.Label(shell, textvariable=self.status_var).pack(side='bottom', anchor='w', pady=(12, 0))
        self.notebook = ttk.Notebook(shell)
        self.notebook.pack(fill='both', expand=True)
        schedule_tab = ttk.Frame(self.notebook, padding=12)
        timer_tab = ttk.Frame(self.notebook, padding=24)
        countdown_tab = ttk.Frame(self.notebook, padding=24)
        stopwatch_tab = ttk.Frame(self.notebook, padding=24)
        sounds_tab = ttk.Frame(self.notebook, padding=24)
        for frame, label in ((schedule_tab, 'Schedules'), (timer_tab, 'Pomodoro'),
                             (countdown_tab, 'Timer'), (stopwatch_tab, 'Stopwatch'), (sounds_tab, 'Sounds & alerts')):
            self.notebook.add(frame, text=label)
        self.build_schedules(schedule_tab)
        self.build_timer(timer_tab)
        self.build_countdown(countdown_tab)
        self.build_stopwatch(stopwatch_tab)
        self.build_sounds(sounds_tab)

    def build_schedules(self, parent):
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(0, weight=1)
        sidebar = ttk.Frame(parent, padding=(0, 0, 16, 0))
        sidebar.grid(row=0, column=0, sticky='nsew')
        sidebar.rowconfigure(2, weight=1)
        ttk.Label(sidebar, text='Schedule presets', style='Heading.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Label(sidebar, text='Activate any combination.').grid(row=1, column=0, sticky='w', pady=(4, 10))
        self.preset_tree = ttk.Treeview(sidebar, columns=('status',), show='tree headings', selectmode='browse', height=12)
        self.preset_tree.heading('#0', text='Preset')
        self.preset_tree.heading('status', text='State')
        self.preset_tree.column('#0', width=155, stretch=False)
        self.preset_tree.column('status', width=70, stretch=False)
        self.preset_tree.grid(row=2, column=0, sticky='ns')
        self.preset_tree.bind('<<TreeviewSelect>>', self.on_preset_select)
        group_buttons = ttk.Frame(sidebar)
        group_buttons.grid(row=3, column=0, sticky='ew', pady=(10, 0))
        for index, (label, action) in enumerate((('New', 'create'), ('Rename', 'rename'), ('Duplicate', 'duplicate'), ('Delete', 'delete'))):
            ttk.Button(group_buttons, text=label, width=11, command=lambda action=action: self.preset_action(action)).grid(
                row=index // 2, column=index % 2, padx=2, pady=3, sticky='ew')
        self.toggle_button = ttk.Button(sidebar, text='Activate / Deactivate', command=lambda: self.preset_action('toggle'))
        self.toggle_button.grid(row=4, column=0, sticky='ew', pady=(8, 0))
        ttk.Label(sidebar, text='Deactivating keeps schedules saved.\nUngrouped reminders stay active.', wraplength=225).grid(
            row=5, column=0, sticky='w', pady=(12, 0))

        panel = ttk.Frame(parent)
        panel.grid(row=0, column=1, sticky='nsew')
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        self.filter_var = tk.StringVar(value='All schedules')
        ttk.Label(panel, textvariable=self.filter_var, style='Heading.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 10))
        table = ttk.Frame(panel)
        table.grid(row=1, column=0, sticky='nsew')
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        columns = ('enabled', 'time', 'title', 'days', 'preset')
        self.tree = ttk.Treeview(table, columns=columns, show='headings', selectmode='extended', height=10)
        for column, heading, width in zip(columns, ('On', 'Time', 'Title', 'Days', 'Preset'), (40, 65, 200, 150, 110)):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, minwidth=35, anchor='w')
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>', self.on_select)
        toolbar = ttk.Frame(panel)
        toolbar.grid(row=2, column=0, sticky='ew', pady=9)
        ttk.Button(toolbar, text='New schedule', command=self.clear_form).pack(side='left')
        ttk.Button(toolbar, text='Delete selected', command=self.delete_entry).pack(side='left', padx=6)
        ttk.Label(toolbar, text='Move to').pack(side='left', padx=(6, 4))
        self.bulk_preset_var = tk.StringVar(value='Ungrouped')
        self.bulk_preset_combo = ttk.Combobox(toolbar, textvariable=self.bulk_preset_var, state='readonly', width=16)
        self.bulk_preset_combo.pack(side='left')
        ttk.Button(toolbar, text='Assign', command=self.assign_selected).pack(side='left', padx=6)
        form = ttk.LabelFrame(panel, text='Schedule details', padding=12)
        form.grid(row=3, column=0, sticky='ew')
        form.columnconfigure(3, weight=1)
        self.time_var = tk.StringVar()
        self.title_var = tk.StringVar()
        self.message_var = tk.StringVar()
        self.enabled_var = tk.BooleanVar(value=True)
        self.preset_var = tk.StringVar(value='Ungrouped')
        self.entry_sound_var = tk.StringVar(value='Use default')
        ttk.Label(form, text='Time').grid(row=0, column=0, sticky='w')
        ttk.Entry(form, textvariable=self.time_var, width=8).grid(row=0, column=1, sticky='w', padx=(8, 18))
        ttk.Label(form, text='Title').grid(row=0, column=2, sticky='w')
        ttk.Entry(form, textvariable=self.title_var).grid(row=0, column=3, sticky='ew', padx=(8, 0))
        ttk.Label(form, text='Message').grid(row=1, column=0, sticky='w', pady=10)
        ttk.Entry(form, textvariable=self.message_var).grid(row=1, column=1, columnspan=3, sticky='ew', padx=(8, 0))
        ttk.Label(form, text='Preset').grid(row=2, column=0, sticky='w')
        self.preset_combo = ttk.Combobox(form, textvariable=self.preset_var, state='readonly', width=18)
        self.preset_combo.grid(row=2, column=1, sticky='w', padx=(8, 18))
        ttk.Label(form, text='Sound').grid(row=2, column=2, sticky='w')
        ttk.Combobox(form, textvariable=self.entry_sound_var, values=list(SOUND_LABELS), state='readonly', width=24).grid(
            row=2, column=3, sticky='ew', padx=(8, 0))
        weekdays = ttk.Frame(form)
        weekdays.grid(row=3, column=0, columnspan=4, sticky='w', pady=(12, 0))
        self.day_vars = {}
        for day in DAY_NAMES:
            variable = tk.BooleanVar(value=True)
            self.day_vars[day] = variable
            ttk.Checkbutton(weekdays, text=day.capitalize(), variable=variable).pack(side='left', padx=(0, 5))
        actions = ttk.Frame(form)
        actions.grid(row=4, column=0, columnspan=4, sticky='ew', pady=(12, 0))
        ttk.Checkbutton(actions, text='Enabled', variable=self.enabled_var).pack(side='left')
        ttk.Button(actions, text='Save schedule', command=self.save_entry).pack(side='right')
        self.test_button = ttk.Button(actions, text='Test alert', command=self.test_notification)
        self.test_button.pack(side='right', padx=8)

    def build_timer(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        clock = ttk.Frame(parent, padding=(12, 20))
        clock.grid(row=0, column=0, sticky='nsew', padx=(0, 32))
        self.phase_var = tk.StringVar(value='Focus')
        self.countdown_var = tk.StringVar(value='25:00')
        self.timer_status_var = tk.StringVar(value='Ready to focus')
        self.sessions_var = tk.StringVar(value='0 focus sessions completed')
        self.timer_progress = tk.DoubleVar(value=0)
        ttk.Label(clock, textvariable=self.phase_var, style='Title.TLabel').pack(pady=(0, 12))
        ttk.Label(clock, textvariable=self.countdown_var, style='Timer.TLabel').pack()
        ttk.Label(clock, textvariable=self.timer_status_var).pack(pady=(8, 20))
        ttk.Progressbar(clock, variable=self.timer_progress, maximum=100).pack(fill='x', pady=(0, 18))
        controls = ttk.Frame(clock)
        controls.pack()
        self.start_button = ttk.Button(controls, text='Start', command=lambda: self.timer_command('start'))
        self.start_button.pack(side='left', padx=4)
        self.pause_button = ttk.Button(controls, text='Pause', command=lambda: self.timer_command('pause'))
        self.pause_button.pack(side='left', padx=4)
        other = ttk.Frame(clock)
        other.pack(pady=(10, 22))
        ttk.Button(other, text='Skip phase', command=lambda: self.timer_command('skip')).pack(side='left', padx=4)
        ttk.Button(other, text='Reset', command=lambda: self.timer_command('reset')).pack(side='left', padx=4)
        ttk.Label(clock, textvariable=self.sessions_var).pack()
        ttk.Label(clock, text='The timer continues after this window closes\nwhile the reminder service is running.', justify='center').pack(pady=22)
        options = ttk.LabelFrame(parent, text='Your focus rhythm', padding=20)
        options.grid(row=0, column=1, sticky='new')
        options.columnconfigure(1, weight=1)
        self.pomodoro_vars = {}
        for row, (key, label, upper) in enumerate((
            ('focus_minutes', 'Focus (minutes)', 240), ('short_break_minutes', 'Short break (minutes)', 240),
            ('long_break_minutes', 'Long break (minutes)', 240), ('long_break_every', 'Long break every N sessions', 12),
        )):
            variable = tk.StringVar()
            self.pomodoro_vars[key] = variable
            ttk.Label(options, text=label).grid(row=row, column=0, sticky='w', pady=7)
            ttk.Spinbox(options, from_=1, to=upper, textvariable=variable, width=7).grid(row=row, column=1, sticky='e', padx=(20, 0))
        for row, (key, label) in enumerate((('auto_start_breaks', 'Start breaks automatically'), ('auto_start_focus', 'Start focus sessions automatically')), start=4):
            variable = tk.BooleanVar()
            self.pomodoro_vars[key] = variable
            ttk.Checkbutton(options, text=label, variable=variable).grid(row=row, column=0, columnspan=2, sticky='w', pady=7)
        self.timer_sound_var = tk.StringVar(value='Use default')
        ttk.Label(options, text='Completion sound').grid(row=6, column=0, sticky='w', pady=(14, 6))
        ttk.Combobox(options, values=list(SOUND_LABELS), state='readonly', textvariable=self.timer_sound_var).grid(row=7, column=0, columnspan=2, sticky='ew')
        ttk.Button(options, text='Save timer settings', command=self.save_timer_settings).grid(row=8, column=0, columnspan=2, sticky='ew', pady=(20, 0))
        ttk.Label(options, text='Changes apply to the next phase.\nReset to apply them to an idle timer.', wraplength=300).grid(row=9, column=0, columnspan=2, sticky='w', pady=(12, 0))

    def build_countdown(self, parent):
        ttk.Label(parent, text='Countdown timer', style='Title.TLabel').pack(pady=(12, 20))
        self.single_countdown_var = tk.StringVar(value='00:05:00')
        self.single_status_var = tk.StringVar(value='Ready')
        ttk.Label(parent, textvariable=self.single_countdown_var, style='Timer.TLabel').pack()
        ttk.Label(parent, textvariable=self.single_status_var).pack(pady=(10, 24))
        duration = ttk.Frame(parent)
        duration.pack()
        self.duration_vars, self.duration_inputs = {}, []
        for column, (key, label, maximum) in enumerate((('hours', 'Hours', 99), ('minutes', 'Minutes', 59), ('seconds', 'Seconds', 59))):
            var = tk.StringVar(value='0')
            self.duration_vars[key] = var
            ttk.Label(duration, text=label).grid(row=0, column=column, padx=15, pady=6)
            entry = ttk.Spinbox(duration, from_=0, to=maximum, width=7, textvariable=var)
            entry.grid(row=1, column=column, padx=15)
            self.duration_inputs.append(entry)
        self.countdown_sound_var = tk.StringVar(value='Use default')
        ttk.Label(parent, text='Completion sound').pack(pady=(22, 6))
        self.countdown_sound_combo = ttk.Combobox(parent, values=list(SOUND_LABELS), state='readonly',
                                                 textvariable=self.countdown_sound_var, width=26)
        self.countdown_sound_combo.pack()
        controls = ttk.Frame(parent)
        controls.pack(pady=24)
        self.countdown_start = ttk.Button(controls, text='Start', command=lambda: self.countdown_command('start'))
        self.countdown_start.pack(side='left', padx=6)
        self.countdown_pause = ttk.Button(controls, text='Pause', command=lambda: self.countdown_command('pause'))
        self.countdown_pause.pack(side='left', padx=6)
        ttk.Button(controls, text='Reset', command=lambda: self.countdown_command('reset')).pack(side='left', padx=6)
        ttk.Label(parent, text='Choose any duration from 1 second to 99 hours, 59 minutes, 59 seconds.\n'
                  'The reminder service alerts you even after this window closes.\n'
                  'Reset to change a running or paused timer.', justify='center').pack(pady=8)

    def build_stopwatch(self, parent):
        ttk.Label(parent, text='Stopwatch', style='Title.TLabel').pack(pady=(12, 16))
        self.stopwatch_time_var = tk.StringVar(value='00:00:00.00')
        self.stopwatch_status_var = tk.StringVar(value='Ready')
        ttk.Label(parent, textvariable=self.stopwatch_time_var, style='Timer.TLabel').pack()
        ttk.Label(parent, textvariable=self.stopwatch_status_var).pack(pady=10)
        controls = ttk.Frame(parent)
        controls.pack(pady=12)
        self.stopwatch_start = ttk.Button(controls, text='Start', command=lambda: self.stopwatch_command('start'))
        self.stopwatch_start.pack(side='left', padx=6)
        self.stopwatch_pause = ttk.Button(controls, text='Pause', command=lambda: self.stopwatch_command('pause'))
        self.stopwatch_pause.pack(side='left', padx=6)
        self.stopwatch_lap = ttk.Button(controls, text='Lap', command=lambda: self.stopwatch_command('lap'))
        self.stopwatch_lap.pack(side='left', padx=6)
        ttk.Button(controls, text='Reset', command=lambda: self.stopwatch_command('reset')).pack(side='left', padx=6)
        table = ttk.Frame(parent)
        table.pack(fill='both', expand=True, padx=90, pady=14)
        self.lap_tree = ttk.Treeview(table, columns=('lap', 'split', 'total'), show='headings', height=7)
        for key, label in (('lap', 'Lap'), ('split', 'Lap time'), ('total', 'Total elapsed')):
            self.lap_tree.heading(key, text=label)
            self.lap_tree.column(key, anchor='center', width=160)
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.lap_tree.yview)
        scroll.pack(side='right', fill='y')
        self.lap_tree.configure(yscrollcommand=scroll.set)
        self.lap_tree.pack(fill='both', expand=True)
        self.rendered_laps = ()
        ttk.Label(parent, text='Elapsed time and up to 100 laps are saved. Closing the window keeps time running.').pack()

    def build_sounds(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(1, weight=1)
        ttk.Label(parent, text='Find your cue', style='Title.TLabel').grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 16))
        catalog = ttk.Frame(parent)
        catalog.grid(row=1, column=0, sticky='nsew', padx=(0, 30))
        catalog.rowconfigure(0, weight=1)
        catalog.columnconfigure(0, weight=1)
        self.sound_tree = ttk.Treeview(catalog, columns=('category',), show='tree headings', selectmode='browse', height=16)
        self.sound_tree.heading('#0', text='Ringtone')
        self.sound_tree.heading('category', text='Collection')
        self.sound_tree.column('#0', width=240)
        self.sound_tree.column('category', width=100)
        self.sound_tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(catalog, orient='vertical', command=self.sound_tree.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.sound_tree.configure(yscrollcommand=scroll.set)
        self.sound_tree.insert('', 'end', iid='silent', text='Silent', values=('Mute',))
        for item in RINGTONES:
            self.sound_tree.insert('', 'end', iid=item.id, text=item.name, values=(item.category,))
        self.sound_tree.bind('<<TreeviewSelect>>', self.on_sound_select)
        settings = ttk.Frame(parent, padding=(0, 12))
        settings.grid(row=1, column=1, sticky='new')
        self.sound_enabled_var = tk.BooleanVar(value=True)
        self.sound_volume_var = tk.StringVar(value='80')
        self.sound_id = DEFAULT_RINGTONE
        self.sound_name_var = tk.StringVar(value='Bright Bell')
        ttk.Label(settings, textvariable=self.sound_name_var, style='Heading.TLabel').pack(anchor='w')
        ttk.Label(settings, text=f'{len(RINGTONES)} original ringtones, available offline.').pack(anchor='w', pady=(8, 16))
        ttk.Checkbutton(settings, text='Play sound with alerts', variable=self.sound_enabled_var).pack(anchor='w')
        volume = ttk.Frame(settings)
        volume.pack(fill='x', pady=12)
        ttk.Label(volume, text='Volume (0–100)').pack(side='left')
        ttk.Spinbox(volume, from_=0, to=100, increment=5, textvariable=self.sound_volume_var, width=7).pack(side='left', padx=14)
        self.preview_button = ttk.Button(settings, text='Preview ringtone', command=self.preview_sound)
        self.preview_button.pack(anchor='w', pady=(0, 12))
        ttk.Label(settings, text='Keep notifications on screen').pack(anchor='w', pady=(12, 6))
        self.notification_lifetime_var = tk.StringVar(value='Until dismissed')
        self.notification_lifetimes = dict(NOTIFICATION_LIFETIMES)
        self.notification_lifetime_combo = ttk.Combobox(settings, textvariable=self.notification_lifetime_var,
                                                       values=list(self.notification_lifetimes), state='readonly', width=26)
        self.notification_lifetime_combo.pack(anchor='w')
        ttk.Label(settings, text='Persistent alerts stay visible on supported desktops.\nClick a notification to dismiss it.', wraplength=350).pack(anchor='w', pady=(8, 16))
        ttk.Button(settings, text='Save sound & alert settings', command=self.save_sound_settings).pack(anchor='w')
        ttk.Label(settings, text='Each schedule, Pomodoro, and countdown can use\nits own sound. The sound switch mutes all alerts.', wraplength=350).pack(anchor='w', pady=12)
        ttk.Label(settings, text='Preview plays the selected ringtone even when muted.', wraplength=350).pack(anchor='w')

    def preset_labels(self):
        return {'Ungrouped': None, **{item['name']: item['id'] for item in self.config['presets']}}

    def refresh_presets(self):
        for item in self.preset_tree.get_children():
            self.preset_tree.delete(item)
        self.preset_tree.insert('', 'end', iid='all', text='All schedules', values=('',))
        self.preset_tree.insert('', 'end', iid='ungrouped', text='Ungrouped', values=('On',))
        for preset in self.config['presets']:
            count = sum(entry.get('preset_id') == preset['id'] for entry in self.config['schedules'])
            self.preset_tree.insert('', 'end', iid='preset:' + preset['id'], text=f"{preset['name']} ({count})", values=('Active' if preset['enabled'] else 'Off',))
        if not self.preset_tree.exists(self.filter_id):
            self.filter_id = 'all'
        self.preset_tree.selection_set(self.filter_id)
        self.refresh_filter_heading()
        choices = list(self.preset_labels())
        self.preset_combo.configure(values=choices)
        self.bulk_preset_combo.configure(values=choices)
        for variable in (self.preset_var, self.bulk_preset_var):
            if variable.get() not in choices:
                variable.set('Ungrouped')

    def refresh_tree(self):
        selection = set(self.tree.selection())
        for item in self.tree.get_children():
            self.tree.delete(item)
        names = {item['id']: item['name'] for item in self.config['presets']}
        for entry in self.config['schedules']:
            group = entry.get('preset_id')
            if self.filter_id == 'ungrouped' and group is not None:
                continue
            if self.filter_id.startswith('preset:') and group != self.filter_id[7:]:
                continue
            active = presets.schedule_is_active(entry, self.config['presets'])
            self.tree.insert('', 'end', iid=entry['id'], values=(
                '✓' if active else '—', entry['time'], entry['title'], ' '.join(entry['days']), names.get(group, 'Ungrouped'),
            ))
        retained = [key for key in selection if self.tree.exists(key)]
        if retained:
            self.tree.selection_set(retained)
        active_count = sum(presets.schedule_is_active(entry, self.config['presets']) for entry in self.config['schedules'])
        self.status_var.set(f'{active_count} active schedules · {len(self.config["schedules"])} saved')

    def on_preset_select(self, _event=None):
        selected = self.preset_tree.selection()
        if not selected or selected[0] == self.filter_id:
            return
        self.filter_id = selected[0]
        self.refresh_filter_heading()
        if self.filter_id.startswith('preset:'):
            self.bulk_preset_var.set(presets.find_preset(self.config, self.filter_id[7:])['name'])
        self.clear_form()
        self.refresh_tree()

    def refresh_filter_heading(self):
        label = 'All schedules' if self.filter_id == 'all' else 'Ungrouped'
        if self.filter_id.startswith('preset:'):
            preset = presets.find_preset(self.config, self.filter_id[7:])
            label = preset['name'] + (' · Active' if preset['enabled'] else ' · Inactive')
            self.toggle_button.configure(text='Deactivate preset' if preset['enabled'] else 'Activate preset', state='normal')
        else:
            self.toggle_button.configure(text='Activate / Deactivate', state='disabled')
        self.filter_var.set(label)

    def on_select(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        entry = next((item for item in self.config['schedules'] if item['id'] == selected[0]), None)
        if entry is None:
            return
        self.selected_id = entry['id']
        self.time_var.set(entry['time'])
        self.title_var.set(entry['title'])
        self.message_var.set(entry['message'])
        self.enabled_var.set(entry['enabled'])
        self.preset_var.set(next((name for name, identity in self.preset_labels().items() if identity == entry.get('preset_id')), 'Ungrouped'))
        self.entry_sound_var.set(next((label for label, identity in SOUND_LABELS.items() if identity == entry.get('ringtone')), 'Use default'))
        for day, variable in self.day_vars.items():
            variable.set(day in entry['days'])

    def clear_form(self):
        self.selected_id = None
        for item in self.tree.selection():
            self.tree.selection_remove(item)
        self.time_var.set('')
        self.title_var.set('')
        self.message_var.set('')
        self.enabled_var.set(True)
        self.entry_sound_var.set('Use default')
        selected_group = self.filter_id[7:] if self.filter_id.startswith('preset:') else None
        self.preset_var.set(next((name for name, identity in self.preset_labels().items() if identity == selected_group), 'Ungrouped'))
        for variable in self.day_vars.values():
            variable.set(True)

    def validate_time(self, value):
        parse_clock(value)

    def commit_config(self, candidate):
        try:
            candidate = validate_config(candidate)
            revision = save_config(candidate, self.config_path, expected_revision=self.config_revision)
        except ConfigConflictError:
            messagebox.showerror('Configuration changed', 'Another editor changed the configuration. Click Reload, then apply your changes again.')
            return False
        except (OSError, ValueError) as exc:
            messagebox.showerror('Save failed', str(exc))
            return False
        self.config = candidate
        self.config_revision = revision
        return True

    def save_entry(self):
        try:
            clock = self.time_var.get().strip()
            self.validate_time(clock)
            title = self.title_var.get().strip()
            if not title:
                raise ValueError('Title cannot be empty.')
            days = [day for day, variable in self.day_vars.items() if variable.get()]
            if not days:
                raise ValueError('Select at least one day.')
            entry = {
                'id': self.selected_id or str(uuid.uuid4()), 'time': clock, 'title': title,
                'message': self.message_var.get().strip(), 'days': days, 'enabled': self.enabled_var.get(),
                'preset_id': self.preset_labels()[self.preset_var.get()], 'ringtone': SOUND_LABELS[self.entry_sound_var.get()],
            }
            candidate = deepcopy(self.config)
            for index, existing in enumerate(candidate['schedules']):
                if existing['id'] == self.selected_id:
                    candidate['schedules'][index] = {**existing, **entry}
                    break
            else:
                candidate['schedules'].append(entry)
            if self.commit_config(candidate):
                self.selected_id = entry['id']
                self.refresh_presets()
                self.refresh_tree()
        except (ValueError, KeyError) as exc:
            messagebox.showerror('Invalid schedule', str(exc))

    def delete_entry(self):
        selected = set(self.tree.selection()) or ({self.selected_id} if self.selected_id else set())
        if not selected:
            return
        candidate = deepcopy(self.config)
        candidate['schedules'] = [entry for entry in candidate['schedules'] if entry['id'] not in selected]
        if self.commit_config(candidate):
            self.clear_form()
            self.refresh_presets()
            self.refresh_tree()

    def assign_selected(self):
        try:
            candidate = presets.assign_schedules(self.config, self.tree.selection(), self.preset_labels()[self.bulk_preset_var.get()])
            if self.commit_config(candidate):
                self.clear_form()
                self.refresh_presets()
                self.refresh_tree()
        except (ValueError, KeyError) as exc:
            messagebox.showerror('Could not assign schedules', str(exc))

    def preset_action(self, action):
        try:
            identity = self.filter_id[7:] if self.filter_id.startswith('preset:') else None
            if action != 'create' and identity is None:
                raise ValueError('Select a preset first.')
            if action in ('create', 'rename', 'duplicate'):
                original = presets.find_preset(self.config, identity)['name'] if identity else ''
                initial = original + ' copy' if action == 'duplicate' else original
                name = simpledialog.askstring('Schedule preset', 'Preset name:', initialvalue=initial, parent=self.root)
                if name is None:
                    return
                if action == 'create':
                    candidate, identity = presets.create_preset(self.config, name)
                elif action == 'duplicate':
                    candidate, identity = presets.duplicate_preset(self.config, identity, name)
                else:
                    candidate = presets.rename_preset(self.config, identity, name)
            elif action == 'toggle':
                candidate = presets.toggle_preset(self.config, identity)
            else:
                preset = presets.find_preset(self.config, identity)
                count = sum(entry.get('preset_id') == identity for entry in self.config['schedules'])
                if not messagebox.askyesno('Delete preset', f"Delete {preset['name']} and its {count} schedules?\nUse Deactivate to keep them for later.", parent=self.root):
                    return
                candidate = presets.delete_preset(self.config, identity)
                identity = None
            if self.commit_config(candidate):
                self.filter_id = 'preset:' + identity if identity else 'all'
                self.filter_var.set(presets.find_preset(self.config, identity)['name'] if identity else 'All schedules')
                self.clear_form()
                self.refresh_presets()
                self.refresh_tree()
        except ValueError as exc:
            messagebox.showerror('Could not update preset', str(exc))

    def load_preferences(self):
        settings = self.config['settings']
        timeout = settings['notification_timeout_ms']
        label = next((label for label, value in self.notification_lifetimes.items() if value == timeout), None)
        if label is None:
            label = f'{timeout / 1000:g} seconds (custom)'
            self.notification_lifetimes[label] = timeout
            self.notification_lifetime_combo.configure(values=list(self.notification_lifetimes))
        self.notification_lifetime_var.set(label)
        self.sound_enabled_var.set(settings['sound_enabled'])
        self.sound_volume_var.set(str(settings['volume']))
        self.sound_id = settings['ringtone']
        self.sound_tree.selection_set(self.sound_id)
        self.sound_tree.see(self.sound_id)
        self.on_sound_select()
        for key, variable in self.pomodoro_vars.items():
            variable.set(self.config['pomodoro'][key])
        self.timer_sound_var.set(next(label for label, identity in SOUND_LABELS.items() if identity == self.config['pomodoro']['ringtone']))
        duration = self.config['countdown']['duration_seconds']
        for key, value in zip(('hours', 'minutes', 'seconds'), (duration // 3600, duration // 60 % 60, duration % 60)):
            self.duration_vars[key].set(str(value))
        self.countdown_sound_var.set(next(label for label, identity in SOUND_LABELS.items() if identity == self.config['countdown']['ringtone']))

    def on_sound_select(self, _event=None):
        selection = self.sound_tree.selection()
        if selection:
            self.sound_id = selection[0]
            self.sound_name_var.set('Silent' if self.sound_id == 'silent' else RINGTONE_BY_ID[self.sound_id].name)

    def volume_value(self):
        try:
            volume = int(self.sound_volume_var.get())
        except (ValueError, tk.TclError):
            raise ValueError('Volume must be an integer from 0 to 100.') from None
        if not 0 <= volume <= 100:
            raise ValueError('Volume must be an integer from 0 to 100.')
        return volume

    def preview_sound(self):
        try:
            volume = self.volume_value()
            self.preview_button.configure(state='disabled')
            PLAYER.play(self.sound_id, volume, lambda result: self.results.put((result, self.preview_button, 'Preview finished')))
        except ValueError as exc:
            messagebox.showerror('Invalid volume', str(exc))

    def save_sound_settings(self):
        try:
            candidate = deepcopy(self.config)
            candidate['settings'].update(sound_enabled=self.sound_enabled_var.get(), volume=self.volume_value(), ringtone=self.sound_id)
            candidate['settings']['notification_timeout_ms'] = self.notification_lifetimes[self.notification_lifetime_var.get()]
            if self.commit_config(candidate):
                self.status_var.set('Sound and alert settings saved')
        except ValueError as exc:
            messagebox.showerror('Invalid sound settings', str(exc))

    def save_timer_settings(self):
        try:
            candidate = deepcopy(self.config)
            for key, variable in self.pomodoro_vars.items():
                candidate['pomodoro'][key] = variable.get() if key.startswith('auto_') else int(variable.get())
            candidate['pomodoro']['ringtone'] = SOUND_LABELS[self.timer_sound_var.get()]
            if self.commit_config(candidate):
                self.status_var.set('Timer settings saved; they apply to the next phase')
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror('Invalid timer settings', str(exc))

    def timer_command(self, action):
        try:
            if action == 'start' and not daemon_is_running(self.config_path):
                raise ValueError('The reminder service is not running. Start it before using the timer:\nsystemctl --user start chronocue\n\nWhen running from source, start chronocue.daemon with the same configuration.')
            self.pomodoro.command(action, self.config['pomodoro'])
        except (OSError, ValueError) as exc:
            messagebox.showerror('Timer unavailable', str(exc))

    def timer_tick(self):
        try:
            state = self.pomodoro.snapshot(self.config['pomodoro'])
            remaining = math.ceil(seconds_remaining(state))
            self.phase_var.set(PHASE_NAMES[state['phase']])
            self.countdown_var.set(f'{remaining // 60:02d}:{remaining % 60:02d}')
            labels = {'idle': 'Ready to focus', 'ready': 'Ready when you are', 'paused': 'Paused', 'running': 'In progress'}
            self.timer_status_var.set('Finishing phase…' if state['status'] == 'running' and remaining == 0 else labels[state['status']])
            self.sessions_var.set(f"{state['completed_focus']} focus sessions completed")
            self.timer_progress.set(100 * (1 - min(remaining / max(state['duration_seconds'], 1), 1)))
            self.start_button.configure(text='Resume' if state['status'] == 'paused' else 'Start', state='disabled' if state['status'] == 'running' else 'normal')
            self.pause_button.configure(state='normal' if state['status'] == 'running' else 'disabled')
        except (OSError, ValueError) as exc:
            self.timer_status_var.set(f'Timer unavailable: {exc}. Reset to recover.')
        self.refresh_clocks()
        self.after_ids['timer'] = self.root.after(100, self.timer_tick)

    def countdown_command(self, action):
        try:
            if action == 'start' and not daemon_is_running(self.config_path):
                raise ValueError('Start the ChronoCue reminder service before starting the timer.')
            state = None if action == 'reset' else self.countdown.snapshot(self.config['countdown'])
            if action == 'reset' or (action == 'start' and state['status'] in ('idle', 'finished')):
                hours, minutes, seconds = (int(self.duration_vars[key].get()) for key in ('hours', 'minutes', 'seconds'))
                if not (0 <= hours <= 99 and 0 <= minutes <= 59 and 0 <= seconds <= 59):
                    raise ValueError('Use 0–99 hours and 0–59 minutes and seconds.')
                total = hours * 3600 + minutes * 60 + seconds
                if total == 0:
                    raise ValueError('Choose a duration of at least one second.')
                candidate = deepcopy(self.config)
                candidate['countdown'].update(duration_seconds=total, ringtone=SOUND_LABELS[self.countdown_sound_var.get()])
                if candidate != self.config and not self.commit_config(candidate):
                    return
            self.countdown.command(action, self.config['countdown'])
            self.refresh_clocks()
        except (OSError, ValueError, tk.TclError) as exc:
            messagebox.showerror('Timer unavailable', str(exc))

    def stopwatch_command(self, action):
        try:
            self.stopwatch.command(action)
            self.refresh_clocks()
        except (OSError, ValueError) as exc:
            messagebox.showerror('Stopwatch unavailable', str(exc))

    def refresh_clocks(self):
        try:
            state = self.countdown.snapshot(self.config['countdown'])
            remaining = remaining_seconds(state)
            self.single_countdown_var.set(format_duration(remaining))
            labels = {'idle': 'Ready', 'running': 'Counting down', 'paused': 'Paused', 'finished': 'Time is up'}
            waiting = state['status'] == 'running' and remaining == 0
            self.single_status_var.set('Waiting for the reminder service…' if waiting else labels[state['status']])
            self.countdown_start.configure(text='Resume' if state['status'] == 'paused' else 'Start',
                                           state='disabled' if state['status'] == 'running' else 'normal')
            self.countdown_pause.configure(state='normal' if state['status'] == 'running' else 'disabled')
            editing = state['status'] in ('idle', 'finished')
            for widget in self.duration_inputs:
                widget.configure(state='normal' if editing else 'disabled')
            self.countdown_sound_combo.configure(state='readonly' if editing else 'disabled')
        except (OSError, ValueError) as exc:
            self.single_status_var.set(f'Timer unavailable: {exc}. Reset to recover.')
        try:
            state = self.stopwatch.snapshot()
            self.stopwatch_time_var.set(format_duration(elapsed_seconds(state), fractions=True))
            self.stopwatch_status_var.set({'idle': 'Ready', 'running': 'Running', 'paused': 'Paused'}[state['status']])
            running = state['status'] == 'running'
            self.stopwatch_start.configure(text='Resume' if state['status'] == 'paused' else 'Start', state='disabled' if running else 'normal')
            self.stopwatch_pause.configure(state='normal' if running else 'disabled')
            self.stopwatch_lap.configure(state='normal' if running and len(state['laps']) < 100 else 'disabled')
            laps = tuple(state['laps'])
            if laps != self.rendered_laps:
                for item in self.lap_tree.get_children():
                    self.lap_tree.delete(item)
                previous = 0
                for index, lap in enumerate(laps, 1):
                    item = self.lap_tree.insert('', 'end', values=(index, format_duration(lap - previous, fractions=True), format_duration(lap, fractions=True)))
                    previous = lap
                if laps:
                    self.lap_tree.see(item)
                self.rendered_laps = laps
        except (OSError, ValueError) as exc:
            self.stopwatch_status_var.set(f'Stopwatch unavailable: {exc}. Reset to recover.')

    def test_notification(self):
        title = self.title_var.get().strip() or 'Test Notification'
        message = self.message_var.get().strip() or 'ChronoCue is working.'
        settings = deepcopy(self.config['settings'])
        override = SOUND_LABELS[self.entry_sound_var.get()]
        self.test_button.configure(state='disabled')

        def notify():
            success = send_notification(title, message, settings['notification_timeout_ms'], settings['urgency'])
            if not success:
                self.results.put(((False, 'Notification failed. Check notify-send and your desktop notification service.'), self.test_button, ''))
            elif settings['sound_enabled']:
                PLAYER.play(override or settings['ringtone'], settings['volume'], lambda result: self.results.put((result, self.test_button, 'Test alert sent')))
            else:
                self.results.put(((True, ''), self.test_button, 'Test alert sent'))
        threading.Thread(target=notify, daemon=True, name='chronocue-test-alert').start()

    def drain_results(self):
        while True:
            try:
                (success, error), button, message = self.results.get_nowait()
            except queue.Empty:
                break
            button.configure(state='normal')
            if success:
                self.status_var.set(message)
            else:
                messagebox.showerror('Alert unavailable', error)
        self.after_ids['results'] = self.root.after(100, self.drain_results)

    def close(self):
        for identity in self.after_ids.values():
            self.root.after_cancel(identity)
        self.root.destroy()

    def reload_config(self):
        try:
            config, revision = load_config_snapshot(self.config_path)
            self.config, self.config_revision = config, revision
            self.filter_id = 'all'
            self.clear_form()
            self.refresh_presets()
            self.refresh_tree()
            self.load_preferences()
        except (OSError, ValueError) as exc:
            messagebox.showerror('Reload failed', str(exc))


def main():
    parser = argparse.ArgumentParser(description='ChronoCue schedules, presets, and Pomodoro timer')
    parser.add_argument('--config', help='Override schedule configuration path')
    args = parser.parse_args()
    root = None
    try:
        root = tk.Tk()
        ScheduleEditor(root, resolve_config_path(args.config))
        root.mainloop()
    except (OSError, ValueError, tk.TclError) as exc:
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass
        parser.exit(1, f'ChronoCue editor could not start: {exc}\n')


if __name__ == '__main__':
    main()
