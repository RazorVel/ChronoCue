# Changelog

## 1.3.0

- Save schedule edits by pressing Enter in the Time, Title, or Message field.
- Toggle individual schedules directly from the Status column or with Space;
  distinguish Enabled, Paused, and Preset off states.
- Export human-editable preset templates and existing presets without internal
  IDs, then safely import them with preview, validation, fresh IDs, inactive
  defaults, and copy-on-conflict naming.
- Standardize the default configuration path as
  `~/.config/chronocue/config.json` while preserving the legacy
  `schedule.json` during installer migration.
- Finish remaining ChronoCue rename cleanup and centralize notification title
  formatting.
- Add focused regression tests for schedule interactions, preset transfer,
  installation migration, naming, and notification behavior.

## 1.2.0

- Add independent Timer and Stopwatch tabs with persistent state, pause/resume,
  reset, and saved stopwatch laps.
- Keep desktop notifications visible until dismissed by default on supported
  notification services.
- Improve alert sound responsiveness with bounded concurrent playback.
- Add real application screenshots and a preview gallery to the README.

## 1.1.0

- Create, rename, duplicate, activate, and deactivate schedule presets; assign
  multiple existing schedules to a group without re-creating them.
- Run configurable Pomodoro focus/short-break/long-break cycles in the daemon,
  with pause/resume, skip/reset, optional automatic phase starts, persistent
  countdowns, and completion alerts.
- Choose from 32 original synthesized ringtones, with previews, volume, global
  mute, and individual schedule/Pomodoro overrides.
- Organize the editor into Schedules, Pomodoro, and Sounds tabs.
- Keep legacy schedules working as ungrouped reminders. Preserve config fields,
  conflict detection, and delivery history.
- Persist shared state/cache locations in installed launchers and check for a
  supported Linux audio player during installation.
- Add virtual-display GUI tests, real-process feature integration checks, and
  ringtone validation alongside the existing reliability suite.

## 1.0.0

- Scheduled desktop notifications, hot reload, and a graphical schedule editor.
- Validated configuration, atomic saves, persistent delivery history, and
  midnight grace periods.
- User-level installation, service management, regression tests, and CI.
