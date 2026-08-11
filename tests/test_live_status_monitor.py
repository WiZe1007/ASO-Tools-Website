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
        "last_store_version": "",
        "last_design_fingerprint": "",
    }


def update_state(version="1.0.0", fingerprint="design-v1", updated="1786348525"):
    return {
        "ok": True,
        "version": version,
        "release_marker": app.google_play_release_marker(version, updated),
        "design_fingerprint": fingerprint,
        "card_meta": {
            "app_id": "com.example.game",
            "name": "Example Game",
            "category": "Game",
            "content_rating": "Everyone",
            "icon_url": "https://example.com/icon.png",
            "screenshots": ["https://example.com/screen.png"],
        },
        "error": "",
    }


class LiveStatusMonitorTests(unittest.TestCase):
    @patch("app.scrape_google_play_app")
    def test_update_state_uses_updated_date_when_version_varies(self, scrape):
        scrape.return_value = {
            "title": "Example Game",
            "version": "Varies with device",
            "updated": 1786348525,
            "genre": "Game",
            "contentRating": "Everyone",
            "icon": "https://example.com/icon=w240-rw",
            "headerImage": "https://example.com/header=w1024-rw",
            "screenshots": ["https://example.com/screen=w720-rw"],
        }

        state = app.fetch_google_play_update_state("com.example.game", "US")

        self.assertTrue(state["ok"])
        self.assertEqual(state["version"], "")
        self.assertEqual(state["release_marker"], "updated:2026-08-10")
        self.assertTrue(state["design_fingerprint"])

    def test_public_updated_marker_parser(self):
        page_html = """
        <div class="meta-row">
          <div class="label">Updated on</div>
          <div class="value">May 4, 2026</div>
        </div>
        """

        self.assertEqual(
            app.parse_google_play_public_updated_marker(page_html),
            "May 4, 2026",
        )

    @patch("app.fetch_google_play_public_updated_marker", return_value="May 4, 2026")
    @patch("app.scrape_google_play_app")
    def test_update_state_falls_back_to_public_updated_date(self, scrape, public_updated):
        scrape.return_value = {
            "title": "Example Game",
            "version": "Varies with device",
            "updated": None,
            "genre": "Game",
            "contentRating": "Everyone",
            "icon": "https://example.com/icon=w240-rw",
            "screenshots": ["https://example.com/screen=w720-rw"],
        }

        state = app.fetch_google_play_update_state("com.example.game", "US")

        self.assertTrue(state["ok"])
        self.assertEqual(state["release_marker"], "updated:2026-05-04")
        public_updated.assert_called_once_with("com.example.game", "US")

    def test_legacy_plain_version_migrates_without_false_alert(self):
        row = app_row()
        row["last_store_version"] = "1.0.0"
        payload = {}

        changes = app.apply_google_play_update_state(
            row,
            payload,
            update_state(version="1.0.0", updated="1786348525"),
        )

        self.assertEqual(changes, [])
        self.assertEqual(
            payload["last_store_version"],
            "version:1.0.0|updated:2026-08-10",
        )

    def test_updated_date_change_is_detected_when_version_stays_the_same(self):
        row = app_row()
        row["last_store_version"] = "version:1.0.0|updated:1786348525"
        payload = {}

        changes = app.apply_google_play_update_state(
            row,
            payload,
            update_state(version="1.0.0", updated="1786434925"),
        )

        self.assertEqual(changes, ["version"])

    def test_timestamp_and_public_date_formats_do_not_create_false_alert(self):
        self.assertFalse(
            app.google_play_release_changed(
                "version:1.0.0|updated:1777852800",
                "version:1.0.0|updated:May 4, 2026",
            )
        )

    def test_new_live_message_includes_app_type(self):
        row = app_row(status="watch", open_codes="", closed_codes="")
        row["app_type"] = "placeholder"
        snapshot = {
            "total": 2,
            "open_codes": ["US"],
            "closed_codes": ["CA"],
        }

        message = app.build_bot_message("new_live", row, snapshot, ["CA"])

        self.assertIn("Тип: <b>Заглушка</b>", message)

    def test_new_live_message_keeps_legacy_rows_without_type(self):
        row = app_row(status="watch", open_codes="", closed_codes="")
        snapshot = {
            "total": 2,
            "open_codes": ["US"],
            "closed_codes": ["CA"],
        }

        message = app.build_bot_message("new_live", row, snapshot, ["CA"])

        self.assertNotIn("Тип:", message)

    @patch("app.fetch_google_play_update_state")
    @patch("app.summarize_google_availability")
    @patch("app.probe_google_play_live_status")
    def test_unchanged_live_app_uses_only_quick_probe(self, probe, summarize, fetch_state):
        probe.return_value = {"state": "live", "country": "US", "error": ""}
        fetch_state.return_value = update_state()
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
        self.assertEqual(
            store.updates[0][1]["last_store_version"],
            "version:1.0.0|updated:2026-08-10",
        )
        self.assertEqual(store.updates[0][1]["last_design_fingerprint"], "design-v1")
        self.assertEqual(store.logs, [])

    @patch("app.send_telegram_event_message")
    @patch("app.fetch_google_play_update_state")
    @patch("app.summarize_google_availability")
    @patch("app.probe_google_play_live_status")
    def test_version_change_sends_one_update_alert(
        self,
        probe,
        summarize,
        fetch_state,
        send_event,
    ):
        probe.return_value = {"state": "live", "country": "US", "error": ""}
        fetch_state.return_value = update_state(version="1.1.0", fingerprint="design-v1")
        row = app_row()
        row["last_store_version"] = "1.0.0"
        row["last_design_fingerprint"] = "design-v1"
        store = FakeStore([row])

        result = app.run_live_status_bot_check(store=store, send_messages=True, write_changes=True)

        self.assertEqual(len(result["notifications"]), 1)
        self.assertEqual(result["notifications"][0]["event"], "app_updated")
        self.assertEqual(result["notifications"][0]["changes"], ["version"])
        self.assertEqual(
            store.updates[0][1]["last_store_version"],
            "version:1.1.0|updated:2026-08-10",
        )
        self.assertEqual(store.logs[0][0], "app_updated")
        self.assertIn("changes=version", store.logs[0][3])
        message = send_event.call_args.args[0]
        self.assertIn("Додаток Оновився", message)
        send_event.assert_called_once()
        summarize.assert_not_called()

    @patch("app.send_telegram_event_message")
    @patch("app.fetch_google_play_update_state")
    @patch("app.summarize_google_availability")
    @patch("app.probe_google_play_live_status")
    def test_design_change_sends_one_update_alert(
        self,
        probe,
        summarize,
        fetch_state,
        send_event,
    ):
        probe.return_value = {"state": "live", "country": "US", "error": ""}
        fetch_state.return_value = update_state(version="1.0.0", fingerprint="design-v2")
        row = app_row()
        row["last_store_version"] = "1.0.0"
        row["last_design_fingerprint"] = "design-v1"
        store = FakeStore([row])

        result = app.run_live_status_bot_check(store=store, send_messages=True, write_changes=True)

        self.assertEqual(result["notifications"][0]["event"], "app_updated")
        self.assertEqual(result["notifications"][0]["changes"], ["design"])
        self.assertEqual(store.updates[0][1]["last_design_fingerprint"], "design-v2")
        self.assertIn("changes=design", store.logs[0][3])
        send_event.assert_called_once()
        summarize.assert_not_called()

    @patch("app.send_telegram_event_message", side_effect=RuntimeError("telegram unavailable"))
    @patch("app.fetch_google_play_update_state")
    @patch("app.summarize_google_availability")
    @patch("app.probe_google_play_live_status")
    def test_failed_update_alert_is_not_marked_as_delivered(
        self,
        probe,
        summarize,
        fetch_state,
        send_event,
    ):
        probe.return_value = {"state": "live", "country": "US", "error": ""}
        fetch_state.return_value = update_state(version="1.1.0", fingerprint="design-v1")
        row = app_row()
        row["last_store_version"] = "1.0.0"
        row["last_design_fingerprint"] = "design-v1"
        store = FakeStore([row])

        with self.assertRaisesRegex(RuntimeError, "telegram unavailable"):
            app.run_live_status_bot_check(store=store, send_messages=True, write_changes=True)

        self.assertEqual(store.updates, [])
        self.assertEqual(store.logs, [])
        send_event.assert_called_once()
        summarize.assert_not_called()

    @patch("app.send_telegram_event_message", side_effect=RuntimeError("telegram unavailable"))
    @patch("app.summarize_google_availability")
    @patch("app.GoogleSheetsAvailabilityStore")
    def test_failed_geo_alert_is_not_marked_as_delivered(
        self,
        store_factory,
        summarize,
        send_event,
    ):
        row = app_row(open_codes="US,CA", closed_codes="")
        store = FakeStore([row])
        store_factory.return_value = store
        summarize.return_value = {
            "total": 2,
            "open_codes": ["US"],
            "closed_codes": ["CA"],
            "not_found_codes": [],
            "no_install_codes": ["CA"],
            "transient_codes": [],
            "is_live": True,
        }

        with self.assertRaisesRegex(RuntimeError, "telegram unavailable"):
            app.run_availability_bot_check(send_messages=True, write_changes=True)

        self.assertEqual(store.updates, [])
        self.assertEqual(store.logs, [])
        send_event.assert_called_once()

    @patch("app.send_telegram_event_message")
    @patch("app.fetch_google_play_update_state")
    @patch("app.summarize_google_availability")
    @patch("app.probe_google_play_live_status")
    def test_metadata_failure_keeps_previous_state_without_alert(
        self,
        probe,
        summarize,
        fetch_state,
        send_event,
    ):
        probe.return_value = {"state": "live", "country": "US", "error": ""}
        fetch_state.return_value = {"ok": False, "error": "TEMPORARY_ERROR"}
        row = app_row()
        row["last_store_version"] = "1.0.0"
        row["last_design_fingerprint"] = "design-v1"
        store = FakeStore([row])

        result = app.run_live_status_bot_check(store=store, send_messages=True, write_changes=True)

        self.assertEqual(result["notifications"], [])
        self.assertNotIn("last_store_version", store.updates[0][1])
        self.assertNotIn("last_design_fingerprint", store.updates[0][1])
        self.assertEqual(store.updates[0][1]["last_error"], "TEMPORARY_ERROR")
        self.assertEqual(result["errors"][0]["stage"], "google_play_metadata")
        self.assertEqual(store.logs, [])
        send_event.assert_not_called()
        summarize.assert_not_called()

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

    @patch("app.fetch_google_play_update_state")
    @patch("app.send_telegram_event_message")
    @patch("app.summarize_google_availability")
    @patch("app.probe_google_play_live_status")
    def test_banned_app_restored_to_live(self, probe, summarize, send_event, fetch_state):
        probe.return_value = {"state": "live", "country": "UA", "error": ""}
        fetch_state.return_value = update_state()
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

    @patch("app.fetch_google_play_update_state")
    @patch("app.send_telegram_event_message")
    @patch("app.summarize_google_availability")
    @patch("app.probe_google_play_live_status")
    def test_watch_app_live_probe_triggers_full_scan(self, probe, summarize, send_event, fetch_state):
        probe.return_value = {"state": "live", "country": "UA", "error": ""}
        fetch_state.return_value = update_state()
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
