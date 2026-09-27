import unittest
from datetime import datetime

from chronocue.daemon import entry_is_due


class ScheduleTest(unittest.TestCase):
    def test_exact_time(self):
        entry = {
            "time": "13:00",
            "days": ["sun"],
            "enabled": True
        }

        now = datetime(
            2026,
            9,
            27,
            13,
            0,
            10
        )

        self.assertTrue(
            entry_is_due(
                entry,
                now,
                120
            )
        )

    def test_too_late(self):
        entry = {
            "time": "13:00",
            "days": ["sun"],
            "enabled": True
        }

        now = datetime(
            2026,
            9,
            27,
            13,
            5
        )

        self.assertFalse(
            entry_is_due(
                entry,
                now,
                120
            )
        )

    def test_wrong_day(self):
        entry = {
            "time": "13:00",
            "days": ["mon"],
            "enabled": True
        }

        now = datetime(
            2026,
            9,
            27,
            13,
            0
        )

        self.assertFalse(
            entry_is_due(
                entry,
                now,
                120
            )
        )


if __name__ == "__main__":
    unittest.main()