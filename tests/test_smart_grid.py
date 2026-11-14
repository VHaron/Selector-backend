import unittest
from selector_backend.smart_grid import optimize_smart_grid


class SmartGridOptimizerTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "settings": {
                "budget_usd": 10,
                "max_tables": 1,
                "session_start": "2026-11-14T18:00",
                "session_end": "2026-11-15T02:00",
                "usd_per_eur": 1.08,
                "usd_per_rub": 0.011,
            },
            "locked": [],
            "tournaments": [
                {"id": "a", "room": "GGNet", "name": "A", "buyin": "$2", "ev_mtt": "$1", "roi_str": "20%", "priority": "1", "duration": "2:00", "smart_start_at": "2026-11-14T18:00", "gtd": "$100", "afs": "100"},
                {"id": "b", "room": "iPoker", "name": "B", "buyin": "€2", "ev_mtt": "$2", "roi_str": "30%", "priority": "1", "duration": "2:00", "smart_start_at": "2026-11-14T18:30", "gtd": "$100", "afs": "100"},
                {"id": "c", "room": "WPN", "name": "C", "buyin": "$2", "ev_mtt": "$1", "roi_str": "20%", "priority": "2", "duration": "2:00", "smart_start_at": "2026-11-14T20:30", "gtd": "$100", "afs": "100"},
                {"id": "too_expensive", "room": "GGNet", "name": "High", "buyin": "$5.60", "ev_mtt": "$9", "roi_str": "100%", "priority": "1", "duration": "1:00", "smart_start_at": "2026-11-14T21:00", "gtd": "$100", "afs": "100"},
            ],
        }

    def test_respects_overlap_and_usd_buyin_cap(self):
        result = optimize_smart_grid(self.payload)
        ids = {item["id"] for item in result["selected"]}
        self.assertNotIn("too_expensive", ids)
        self.assertLessEqual(result["summary"]["peak_tables"], 1)
        self.assertLessEqual(result["summary"]["spent_usd"], 10)

    def test_locked_grid_is_preserved_and_counted(self):
        payload = dict(self.payload)
        payload["locked"] = [
            {"id": "manual", "room": "WPN", "name": "Manual", "buyin": "$3", "duration": "2:00", "smart_start_at": "2026-11-14T18:00"}
        ]
        result = optimize_smart_grid(payload)
        self.assertEqual(result["summary"]["locked_count"], 1)
        self.assertNotIn("manual", {item["id"] for item in result["selected"]})


if __name__ == "__main__":
    unittest.main()
