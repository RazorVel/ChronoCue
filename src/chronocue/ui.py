import argparse
from copy import deepcopy
import uuid
import tkinter as tk

from tkinter import messagebox, ttk

from .config import (
    ConfigConflictError,
    DAY_NAMES,
    load_config_snapshot,
    parse_clock,
    resolve_config_path,
    save_config
)
from .notifier import send_notification


class ScheduleEditor:
    def __init__(self, root, config_path):
        self.root = root
        self.config_path = config_path
        self.config, self.config_revision = load_config_snapshot(config_path)

        self.selected_id = None

        root.title("ChronoCue")
        root.geometry("1000x600")
        root.minsize(850, 500)

        self.build_ui()
        self.refresh_tree()

    def build_ui(self):
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        ttk.Label(
            main,
            text=f"Config: {self.config_path}"
        ).grid(
            row=0,
            column=0,
            sticky="w",
            pady=(0, 10)
        )

        columns = (
            "enabled",
            "time",
            "title",
            "message",
            "days"
        )

        self.tree = ttk.Treeview(
            main,
            columns=columns,
            show="headings",
            selectmode="browse"
        )

        self.tree.heading("enabled", text="On")
        self.tree.heading("time", text="Time")
        self.tree.heading("title", text="Title")
        self.tree.heading("message", text="Message")
        self.tree.heading("days", text="Days")

        self.tree.column("enabled", width=50, anchor="center")
        self.tree.column("time", width=80, anchor="center")
        self.tree.column("title", width=180)
        self.tree.column("message", width=300)
        self.tree.column("days", width=180)

        self.tree.grid(
            row=1,
            column=0,
            sticky="nsew"
        )

        self.tree.bind(
            "<<TreeviewSelect>>",
            self.on_select
        )

        form = ttk.LabelFrame(
            main,
            text="Schedule",
            padding=10
        )

        form.grid(
            row=2,
            column=0,
            sticky="ew",
            pady=(12, 0)
        )

        form.columnconfigure(3, weight=1)

        self.time_var = tk.StringVar()
        self.title_var = tk.StringVar()
        self.message_var = tk.StringVar()
        self.enabled_var = tk.BooleanVar(value=True)

        ttk.Label(
            form,
            text="Time"
        ).grid(
            row=0,
            column=0,
            sticky="w"
        )

        ttk.Entry(
            form,
            textvariable=self.time_var,
            width=8
        ).grid(
            row=0,
            column=1,
            sticky="w",
            padx=(5, 20)
        )

        ttk.Label(
            form,
            text="Title"
        ).grid(
            row=0,
            column=2,
            sticky="w"
        )

        ttk.Entry(
            form,
            textvariable=self.title_var
        ).grid(
            row=0,
            column=3,
            sticky="ew",
            padx=(5, 0)
        )

        ttk.Label(
            form,
            text="Message"
        ).grid(
            row=1,
            column=0,
            sticky="w",
            pady=(10, 0)
        )

        ttk.Entry(
            form,
            textvariable=self.message_var
        ).grid(
            row=1,
            column=1,
            columnspan=3,
            sticky="ew",
            padx=(5, 0),
            pady=(10, 0)
        )

        days_frame = ttk.Frame(form)

        days_frame.grid(
            row=2,
            column=0,
            columnspan=4,
            sticky="w",
            pady=(10, 0)
        )

        self.day_vars = {}

        for day in DAY_NAMES:
            variable = tk.BooleanVar(value=True)
            self.day_vars[day] = variable

            ttk.Checkbutton(
                days_frame,
                text=day.capitalize(),
                variable=variable
            ).pack(
                side="left",
                padx=(0, 8)
            )

        ttk.Checkbutton(
            days_frame,
            text="Enabled",
            variable=self.enabled_var
        ).pack(
            side="left",
            padx=(20, 0)
        )

        buttons = ttk.Frame(main)

        buttons.grid(
            row=3,
            column=0,
            sticky="ew",
            pady=(12, 0)
        )

        ttk.Button(
            buttons,
            text="New",
            command=self.clear_form
        ).pack(side="left")

        ttk.Button(
            buttons,
            text="Add / Update",
            command=self.save_entry
        ).pack(
            side="left",
            padx=(8, 0)
        )

        ttk.Button(
            buttons,
            text="Delete",
            command=self.delete_entry
        ).pack(
            side="left",
            padx=(8, 0)
        )

        ttk.Button(
            buttons,
            text="Test Notification",
            command=self.test_notification
        ).pack(
            side="left",
            padx=(8, 0)
        )

        ttk.Button(
            buttons,
            text="Reload",
            command=self.reload_config
        ).pack(
            side="right"
        )

    def refresh_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        for index, entry in enumerate(
            self.config["schedules"]
        ):
            days = entry.get("days", DAY_NAMES)

            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    "✓" if entry.get("enabled", True) else "",
                    entry.get("time", ""),
                    entry.get("title", ""),
                    entry.get("message", ""),
                    " ".join(days)
                )
            )

    def on_select(self, _event=None):
        selection = self.tree.selection()

        if not selection:
            return

        index = int(selection[0])

        entry = self.config["schedules"][index]

        self.selected_id = entry["id"]

        self.time_var.set(entry.get("time", ""))
        self.title_var.set(entry.get("title", ""))
        self.message_var.set(entry.get("message", ""))
        self.enabled_var.set(entry.get("enabled", True))

        configured_days = set(
            entry.get("days", DAY_NAMES)
        )

        for day, variable in self.day_vars.items():
            variable.set(day in configured_days)

    def clear_form(self):
        self.selected_id = None

        for item in self.tree.selection():
            self.tree.selection_remove(item)

        self.time_var.set("")
        self.title_var.set("")
        self.message_var.set("")
        self.enabled_var.set(True)

        for variable in self.day_vars.values():
            variable.set(True)

    def validate_time(self, value):
        try:
            parse_clock(value)
        except ValueError:
            raise ValueError(
                "Time must use 24-hour HH:MM format, "
                "for example 13:00."
            ) from None

    def commit_config(self, candidate):
        """Keep the displayed configuration unchanged until disk commit succeeds."""
        try:
            revision = save_config(
                candidate,
                self.config_path,
                expected_revision=self.config_revision
            )
        except ConfigConflictError:
            messagebox.showerror(
                "Configuration changed",
                "The configuration was changed by another editor or process. "
                "Your changes have not been saved. Click Reload to load the "
                "latest configuration, then apply your changes again."
            )
            return False
        except (OSError, ValueError) as exc:
            messagebox.showerror("Save failed", str(exc))
            return False

        self.config = candidate
        self.config_revision = revision
        return True

    def save_entry(self):
        try:
            schedule_time = self.time_var.get().strip()

            self.validate_time(schedule_time)

            title = self.title_var.get().strip()
            message = self.message_var.get().strip()

            if not title:
                raise ValueError("Title cannot be empty.")

            days = [
                day
                for day, variable
                in self.day_vars.items()
                if variable.get()
            ]

            if not days:
                raise ValueError(
                    "Select at least one day."
                )

            entry = {
                "id": self.selected_id or str(uuid.uuid4()),
                "time": schedule_time,
                "title": title,
                "message": message,
                "days": days,
                "enabled": self.enabled_var.get()
            }

            candidate = deepcopy(self.config)
            found = False

            if self.selected_id:
                for index, existing in enumerate(
                    candidate["schedules"]
                ):
                    if existing.get("id") == self.selected_id:
                        candidate["schedules"][index] = {**existing, **entry}
                        found = True
                        break

            if not found:
                candidate["schedules"].append(entry)

            if not self.commit_config(candidate):
                return

            self.selected_id = entry["id"]

            self.refresh_tree()

        except ValueError as exc:
            messagebox.showerror(
                "Invalid schedule",
                str(exc)
            )

    def delete_entry(self):
        if not self.selected_id:
            return

        candidate = deepcopy(self.config)
        candidate["schedules"] = [
            entry
            for entry in candidate["schedules"]
            if entry.get("id") != self.selected_id
        ]

        if not self.commit_config(candidate):
            return

        self.clear_form()
        self.refresh_tree()

    def test_notification(self):
        title = (
            self.title_var.get().strip()
            or "Test Notification"
        )

        message = (
            self.message_var.get().strip()
            or "ChronoCue is working."
        )

        settings = self.config["settings"]

        success = send_notification(
            title,
            message,
            int(
                settings.get(
                    "notification_timeout_ms",
                    10000
                )
            ),
            settings.get(
                "urgency",
                "normal"
            )
        )

        if not success:
            messagebox.showerror(
                "Notification failed",
                "notify-send failed. Check that "
                "libnotify-bin and a notification "
                "daemon are available."
            )

    def reload_config(self):
        try:
            config, revision = load_config_snapshot(
                self.config_path
            )

            self.config = config
            self.config_revision = revision
            self.clear_form()
            self.refresh_tree()

        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "Reload failed",
                str(exc)
            )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--config",
        help="Override schedule configuration path"
    )

    args = parser.parse_args()

    config_path = resolve_config_path(
        args.config
    )

    root = None
    try:
        root = tk.Tk()
        ScheduleEditor(root, config_path)
        root.mainloop()
    except (OSError, ValueError, tk.TclError) as exc:
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass
        parser.exit(1, f"ChronoCue editor could not start: {exc}\n")


if __name__ == "__main__":
    main()
