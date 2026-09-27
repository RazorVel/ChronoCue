import logging
import shutil
import subprocess


APP_DISPLAY_NAME = "ChronoCue"
NOTIFICATION_PROCESS_TIMEOUT_SECONDS = 10


def send_notification(
    title: str,
    message: str,
    timeout_ms: int = 10000,
    urgency: str = "normal",
) -> bool:
    binary = shutil.which("notify-send")
    if not binary:
        logging.error("notify-send is not installed or not available on PATH")
        return False

    command = [
        binary,
        "--app-name=ChronoCue",
        "--urgency", urgency,
        "--expire-time", str(timeout_ms),
        "--",
        f"[{APP_DISPLAY_NAME}] {title}",
        message,
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=NOTIFICATION_PROCESS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logging.error("Could not send notification: %s", exc)
        return False
    return result.returncode == 0
