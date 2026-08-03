import unittest
from unittest.mock import patch

import app


class FakeStore:
    def __init__(self, apps):
        self.apps = apps
        self.updates = []
        self.logs = []

    def load_apps(self):
        return self.apps

    def batch_update_apps(self, updates):
        self.updates.extend(updates)

    def append_log(self, event, app_row, countries, details=""):
        self.logs.append((event, app_row, set(countries), details))


def app_row(status="live", open_codes="US", closed_codes="CA"):
    return {
        "row_index": 2,
        "enabled": "TRUE",
        "status": status,
        "app_id": "com.example.game",
        "app_url": "https://play.google.com/store/apps/details?id=com.example.game",
        "app_name": "Example Game",
        "last_open_countries": open_codes,
        "last_closed_countries": closed_codes,
        "last_closed_count": 1 if closed_codes else 0,
    }


class LiveStatusMonitorTests(unittest.TestCase):
    @patch("app.summarize_google_availability")
    @patch("app.probe_google_play_live_status")
    def test_unchanged_live_app_uses_only_quick_probe(self, probe, summarize):
        probe.return_value = {"state": "live", "country": "US", "error": ""}
        store = FakeStore([app_row()])

        result = app.run_live_status_bot_check(
            store=store,
            send_messages=False,
            write_changes=True,
        )

        self.assertEqual(result["apps_checked"], 1)
        self.assertEqual(result["notifications"], [])
        self.assertEqual(result["full_confirmations"], 0)
        summarize.assert_not_called()
        probe.assert_called_once_with(
            store.apps[0],
            len(app.BOT_LIVE_STATUS_PROBE_COUNTRIES),
        )
        self.assertEqual(len(store.updates), 1)
        self.assertEqual(store.logs, [])

    @patch("app.send_telegram_event_message")
    @patch("app.summarize_google_availability")
    @patch("app.probe_google_play_live_status")
    def test_possible_ban_requires_full_confirmation(self, probe, summarize, send_event):
        probe.return_value = {"state": "candidate_closed", "closed_codes": ["US"]}
        summarize.return_value = {
            "total": 3,
            "open_codes": [],
            "closed_codes": ["US", "CA", "GB"],
            "not_found_codes": ["US", "CA", "GB"],
            "no_install_codes": [],
            "transient_codes": [],
            "is_live": False,
        }
        store = FakeStore([app_row()])

        result = app.run_live_status_bot_check(store=store, send_messages=True, write_changes=True)

        self.assertEqual(result["notifications"][0]["event"], "app_banned")
        self.assertEqual(result["full_confirmations"], 1)
        self.assertEqual(store.updates[0][1]["status"], "banned")
        self.assertEqual(store.logs[0][0], "app_banned")
        send_event.assert_called_once()

    @patch("app.send_telegram_event_message")
    @patch("app.summarize_google_availability")
    @patch("app.probe_google_play_live_status")
    def test_no_install_signals_do_not_ban_app(self, probe, summarize, send_event):
        probe.return_value = {"state": "candidate_closed", "closed_codes": ["US", "UA"]}
        summarize.return_value = {
            "total": 3,
            "open_codes": [],
            "closed_codes": ["US", "CA", "GB"],
            "not_found_codes": [],
            "no_install_codes": ["US", "CA", "GB"],
            "transient_codes": [],
            "is_live": False,
        }
        store = FakeStore([app_row()])

        result = app.run_live_status_bot_check(store=store, send_messages=True, write_changes=True)

        self.assertEqual(result["notifications"], [])
        self.assertEqual(result["full_confirmations"], 1)
        self.assertEqual(store.updates[0][1]["status"], "live")
        self.assertEqual(store.logs, [])
        send_event.assert_not_called()

    @patch("app.send_telegram_event_message")
    @patch("app.summarize_google_availability")
    @patch("app.probe_google_play_live_status")
    def test_banned_app_restored_to_live(self, probe, summarize, send_event):
        probe.return_value = {"state": "live", "country": "UA", "error": ""}
        summarize.return_value = {
            "total": 3,
            "open_codes": ["US", "GB"],
            "closed_codes": ["CA"],
            "transient_codes": [],
            "is_live": True,
        }
        store = FakeStore([app_row(status="banned", open_codes="", closed_codes="US,CA,GB")])

        result = app.run_live_status_bot_check(store=store, send_messages=True, write_changes=True)

        self.assertEqual(result["notifications"][0]["event"], "app_restored")
        self.assertEqual(store.updates[0][1]["status"], "live")
        self.assertEqual(store.logs[0][0], "app_restored")
        send_event.assert_called_once()

    @patch("app.send_telegram_event_message")
    @patch("app.summarize_google_availability")
    @patch("app.probe_google_play_live_status")
    def test_unchanged_banned_app_does_not_repeat_alert(self, probe, summarize, send_event):
        probe.return_value = {"state": "candidate_closed", "closed_codes": ["US", "UA"]}
        store = FakeStore([app_row(status="banned", open_codes="", closed_codes="US,CA,GB")])

        result = app.run_live_status_bot_check(store=store, send_messages=True, write_changes=True)

        self.assertEqual(result["notifications"], [])
        self.assertNotIn("status", store.updates[0][1])
        self.assertIn("last_checked_at", store.updates[0][1])
        self.assertEqual(store.logs, [])
        send_event.assert_not_called()
        summarize.assert_not_called()

    @patch("app.send_telegram_event_message")
    @patch("app.summarize_google_availability")
    @patch("app.probe_google_play_live_status")
    def test_watch_app_live_probe_triggers_full_scan(self, probe, summarize, send_event):
        probe.return_value = {"state": "live", "country": "UA", "error": ""}
        summarize.return_value = {
            "total": 3,
            "open_codes": ["UA", "US"],
            "closed_codes": ["CA"],
            "transient_codes": [],
            "is_live": True,
        }
        store = FakeStore([app_row(status="watch", open_codes="", closed_codes="")])

        result = app.run_live_status_bot_check(store=store, send_messages=True, write_changes=True)

        self.assertEqual(result["notifications"][0]["event"], "new_live")
        self.assertEqual(result["full_confirmations"], 1)
        self.assertEqual(store.updates[0][1]["status"], "live")
        send_event.assert_called_once()
        probe.assert_called_once_with(
            store.apps[0],
            len(app.BOT_LIVE_STATUS_PROBE_COUNTRIES),
        )

    @patch("app.summarize_google_availability")
    @patch("app.probe_google_play_live_status")
    def test_watch_app_without_probe_live_skips_full_scan(self, probe, summarize):
        probe.return_value = {"state": "candidate_closed", "closed_codes": ["US", "UA"]}
        store = FakeStore([app_row(status="watch", open_codes="", closed_codes="")])

        result = app.run_live_status_bot_check(store=store, send_messages=False, write_changes=True)

        self.assertEqual(result["apps_checked"], 1)
        self.assertEqual(result["full_confirmations"], 0)
        self.assertEqual(result["notifications"], [])
        summarize.assert_not_called()
        probe.assert_called_once_with(
            store.apps[0],
            len(app.BOT_LIVE_STATUS_PROBE_COUNTRIES),
        )


if __name__ == "__main__":
    unittest.main()
