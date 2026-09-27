# Changelog

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
