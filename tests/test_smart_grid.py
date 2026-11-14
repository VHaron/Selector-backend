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

    def test_priority_colors_prefer_green_then_yellow_over_red(self):
        payload = dict(self.payload)
        payload["settings"] = {**self.payload["settings"], "max_tables": 1}
        base = {
            "room": "GGNet", "name": "Same event", "buyin": "$2",
            "ev_mtt": "$1", "roi_str": "20%", "duration": "2:00",
            "smart_start_at": "2026-11-14T19:00", "gtd": "$100", "afs": "100",
        }
        payload["tournaments"] = [
            {**base, "id": "red", "priority": "3"},
            {**base, "id": "yellow", "priority": "2"},
            {**base, "id": "green", "priority": "1"},
        ]
        result = optimize_smart_grid(payload)
        self.assertEqual([item["id"] for item in result["selected"]], ["green"])

    def test_pko_is_preferred_over_non_pko_when_otherwise_equal(self):
        payload = dict(self.payload)
        payload["settings"] = {**self.payload["settings"], "max_tables": 1}
        base = {
            "room": "GGNet", "buyin": "$2", "ev_mtt": "$1", "roi_str": "20%",
            "priority": "1", "duration": "1:00", "smart_start_at": "2026-11-14T19:00",
            "gtd": "$100", "afs": "100",
        }
        payload["tournaments"] = [
            {**base, "id": "regular", "name": "Regular Freezeout"},
            {**base, "id": "pko", "name": "Progressive KO"},
        ]
        result = optimize_smart_grid(payload)
        self.assertEqual([item["id"] for item in result["selected"]], ["pko"])
        self.assertEqual(result["summary"]["selected_pko_count"], 1)

    def test_yellow_pko_can_outrank_green_non_pko(self):
        payload = dict(self.payload)
        payload["settings"] = {**self.payload["settings"], "max_tables": 1}
        base = {
            "room": "GGNet", "buyin": "$2", "ev_mtt": "$1", "roi_str": "20%",
            "duration": "1:00", "smart_start_at": "2026-11-14T19:00",
            "gtd": "$100", "afs": "100",
        }
        payload["tournaments"] = [
            {**base, "id": "green_regular", "name": "Regular", "priority": "1"},
            {**base, "id": "yellow_pko", "name": "PKO", "priority": "2"},
        ]
        result = optimize_smart_grid(payload)
        self.assertEqual([item["id"] for item in result["selected"]], ["yellow_pko"])

    def test_turbo_is_only_used_as_late_session_add_on(self):
        payload = dict(self.payload)
        payload["settings"] = {**self.payload["settings"], "max_tables": 1}
        base = {
            "room": "GGNet", "buyin": "$2", "ev_mtt": "$1", "roi_str": "20%",
            "priority": "1", "duration": "1:00", "gtd": "$100", "afs": "100",
        }
        payload["tournaments"] = [
            {**base, "id": "regular", "name": "Regular", "smart_start_at": "2026-11-14T18:00"},
            {**base, "id": "early_turbo", "name": "Turbo", "smart_start_at": "2026-11-14T20:30"},
            {**base, "id": "late_hyper", "name": "Hyper Turbo", "smart_start_at": "2026-11-15T00:30"},
        ]
        result = optimize_smart_grid(payload)
        ids = {item["id"] for item in result["selected"]}
        self.assertIn("regular", ids)
        self.assertNotIn("early_turbo", ids)
        self.assertIn("late_hyper", ids)
        self.assertEqual(result["summary"]["selected_turbo_count"], 1)

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
