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
        for source, payload in updates:
            row_index = source.get("row_index")
            for row in self.apps:
                if row.get("row_index") == row_index:
                    row.update(payload)
                    break

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
        "pending_store_version": "",
        "pending_design_fingerprint": "",
    }


def update_state(version="1.0.0", fingerprint="design-v1", updated="1786348525", country="US"):
    return {
        "ok": True,
        "country": country,
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


def release_marker(version="1.0.0", updated="1786348525", country="US"):
    return app.google_play_country_release_marker(
        country,
        app.google_play_release_marker(version, updated),
    )


def design_marker(fingerprint="design-v1", country="US"):
    return app.google_play_country_design_marker(country, fingerprint)


class LiveStatusMonitorTests(unittest.TestCase):
    def test_deleted_or_edited_app_during_check_is_not_written_or_announced(self):
        row = app_row(status="watch", open_codes="", closed_codes="")
        snapshot = {
            "total": 2, "open_codes": ["US", "CA"], "closed_codes": [],
            "not_found_codes": [], "no_install_codes": [], "transient_codes": [], "is_live": True,
        }
        for runner in (app.run_live_status_bot_check, app.run_availability_bot_check):
            for current_rows in ([], [{**row, "app_id": "com.example.changed"}], [{**row, "notes": "Edited"}]):
                with self.subTest(runner=runner.__name__, current=current_rows):
                    store = FakeStore([dict(row)])
                    with (
                        patch.object(store, "load_apps", side_effect=[[dict(row)], current_rows]),
                        patch("app.GoogleSheetsAvailabilityStore", return_value=store),
                        patch("app.probe_google_play_live_status", return_value={"state": "live", "country": "US", "error": ""}),
                        patch("app.fetch_google_play_update_state", return_value=update_state()),
                        patch("app.summarize_google_availability", return_value=snapshot),
                        patch("app.send_telegram_event_message") as send,
                    ):
                        result = runner(send_messages=True, write_changes=True)
                    self.assertEqual(result["notifications"], [])
                    self.assertEqual(result["skipped"][-1]["reason"], "record_changed_or_deleted")
                    self.assertEqual(store.updates, [])
                    self.assertEqual(store.logs, [])
                    send.assert_not_called()

    def test_bot_batch_never_restores_identity_or_user_edited_fields(self):
        store = object.__new__(app.GoogleSheetsAvailabilityStore)
        store.apps_sheet = "Apps"
        row = app_row()
        with patch.object(store, "_request") as request:
            store.batch_update_apps([(row, {
                **row, "owner": "Old owner", "notes": "Old notes", "status": "live",
                "last_checked_at": "2026-09-05T12:00:00+00:00",
            })])
        data = request.call_args.kwargs["json"]["data"]
        ranges = {item["range"] for item in data}
        for column in "ACDEFGN":
            self.assertNotIn(f"'Apps'!{column}2", ranges)
        self.assertIn("'Apps'!H2", ranges)

    def test_google_sheets_schema_adds_pending_update_columns(self):
        store = object.__new__(app.GoogleSheetsAvailabilityStore)
        store.apps_sheet = "Apps"
        store.log_sheet = "Checks"
        writes = []
        store.get_sheet_titles = lambda: {"Apps", "Checks"}
        store.add_sheet = lambda _title: self.fail("Existing sheets must not be recreated")
        store.get_values = lambda _sheet, cell_range: (
            [app.PREVIOUS_APPS_SHEET_HEADERS]
            if cell_range == "A1:R1"
            else [app.CHECKS_SHEET_HEADERS]
        )
        store.update_values = lambda sheet, cell_range, values: writes.append(
            (sheet, cell_range, values)
        )

        store.ensure_ready()

        self.assertEqual(
            writes,
            [(
                "Apps",
                "Q1:R1",
                [["pending_store_version", "pending_design_fingerprint"]],
            )],
        )

    @patch("app.BOT_UPDATE_METADATA_COUNTRY", "US")
    @patch("app.fetch_google_play_update_state")
    def test_update_metadata_uses_stable_country_before_probe_country(self, fetch_state):
        fetch_state.return_value = update_state()
        row = app_row(open_codes="UA,GB")

        state = app.fetch_google_play_update_state_for_app(row, "UA")

        self.assertTrue(state["ok"])
        self.assertEqual(state["country"], "US")
        fetch_state.assert_called_once_with("com.example.game", "US")

    @patch("app.BOT_UPDATE_METADATA_COUNTRY", "US")
    @patch("app.fetch_google_play_update_state")
    def test_update_metadata_falls_back_when_stable_country_fails(self, fetch_state):
        fetch_state.side_effect = [
            {"ok": False, "error": "NOT_AVAILABLE"},
            update_state(),
        ]
        row = app_row(open_codes="UA")

        state = app.fetch_google_play_update_state_for_app(row, "UA")

        self.assertTrue(state["ok"])
        self.assertEqual(state["country"], "UA")
        self.assertEqual(
            fetch_state.call_args_list,
            [
                unittest.mock.call("com.example.game", "US"),
                unittest.mock.call("com.example.game", "UA"),
            ],
        )

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
            "gp-release-v3:US|version:1.0.0|updated:2026-08-10",
        )
        self.assertEqual(payload["last_design_fingerprint"], "gp-design-v3:US:design-v1")

    def test_updated_date_change_is_ignored_when_version_stays_the_same(self):
        row = app_row()
        row["last_store_version"] = release_marker()
        payload = {}

        changes = app.apply_google_play_update_state(
            row,
            payload,
            update_state(version="1.0.0", updated="1786434925"),
        )

        self.assertEqual(changes, [])
        self.assertEqual(payload["pending_store_version"], "")

    def test_country_change_is_a_quiet_baseline_migration(self):
        row = app_row()
        row["last_store_version"] = release_marker(country="UA")
        row["last_design_fingerprint"] = design_marker(country="UA")
        payload = {}

        changes = app.apply_google_play_update_state(
            row,
            payload,
            update_state(version="1.1.0", fingerprint="design-v2", country="US"),
        )

        self.assertEqual(changes, [])
        self.assertEqual(payload["last_store_version"], release_marker(version="1.1.0"))
        self.assertEqual(payload["last_design_fingerprint"], design_marker("design-v2"))

    def test_design_fingerprint_ignores_cdn_params_order_and_duplicates(self):
        first = app.google_play_design_fingerprint(
            "https://play-lh.googleusercontent.com/icon-id=w240-rw?cache=one",
            "https://play-lh.googleusercontent.com/header-id=w1024-rw",
            [
                "https://play-lh.googleusercontent.com/screen-a=w720-rw",
                "https://play-lh.googleusercontent.com/screen-b=s720",
            ],
        )
        second = app.google_play_design_fingerprint(
            "https://play-lh.googleusercontent.com/icon-id=s512",
            "",
            [
                "https://play-lh.googleusercontent.com/screen-b=h1080-rw?x=1",
                "https://play-lh.googleusercontent.com/screen-a=rw-e365",
                "https://play-lh.googleusercontent.com/screen-a=w999-rw",
            ],
        )

        self.assertEqual(first, second)

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
        self.assertEqual(result["metadata_checked"], 1)
        self.assertEqual(result["metadata_baselines"], 1)
        self.assertEqual(result["metadata_changes"], 0)
        self.assertEqual(result["metadata_failures"], 0)
        summarize.assert_not_called()
        probe.assert_called_once_with(
            store.apps[0],
            len(app.BOT_LIVE_STATUS_PROBE_COUNTRIES),
        )
        self.assertEqual(len(store.updates), 1)
        self.assertEqual(
            store.updates[0][1]["last_store_version"],
            release_marker(),
        )
        self.assertEqual(store.updates[0][1]["last_design_fingerprint"], design_marker())
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
        row["last_store_version"] = release_marker()
        row["last_design_fingerprint"] = design_marker()
        store = FakeStore([row])

        first_result = app.run_live_status_bot_check(
            store=store,
            send_messages=True,
            write_changes=True,
        )

        self.assertEqual(first_result["notifications"], [])
        self.assertEqual(row["pending_store_version"], release_marker(version="1.1.0"))
        self.assertEqual(row["last_store_version"], release_marker())
        send_event.assert_not_called()

        second_result = app.run_live_status_bot_check(
            store=store,
            send_messages=True,
            write_changes=True,
        )

        self.assertEqual(len(second_result["notifications"]), 1)
        self.assertEqual(second_result["notifications"][0]["event"], "app_updated")
        self.assertEqual(second_result["notifications"][0]["changes"], ["version"])
        self.assertEqual(
            row["last_store_version"],
            release_marker(version="1.1.0"),
        )
        self.assertEqual(row["pending_store_version"], "")
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
    def test_transient_version_candidate_is_cleared_next_cycle(
        self,
        probe,
        summarize,
        fetch_state,
        send_event,
    ):
        probe.return_value = {"state": "live", "country": "US", "error": ""}
        fetch_state.side_effect = [
            update_state(version="1.1.0", fingerprint="design-v1"),
            update_state(version="1.1.0", fingerprint="design-v1"),
            update_state(version="1.0.0", fingerprint="design-v1"),
        ]
        row = app_row()
        row["last_store_version"] = release_marker()
        row["last_design_fingerprint"] = design_marker()
        store = FakeStore([row])

        first_result = app.run_live_status_bot_check(
            store=store,
            send_messages=True,
            write_changes=True,
        )

        self.assertEqual(first_result["notifications"], [])
        self.assertEqual(row["pending_store_version"], release_marker(version="1.1.0"))

        second_result = app.run_live_status_bot_check(
            store=store,
            send_messages=True,
            write_changes=True,
        )

        self.assertEqual(second_result["notifications"], [])
        self.assertEqual(row["last_store_version"], release_marker())
        self.assertEqual(row["pending_store_version"], "")
        self.assertEqual(store.logs, [])
        send_event.assert_not_called()
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
        row["last_store_version"] = release_marker()
        row["last_design_fingerprint"] = design_marker()
        store = FakeStore([row])

        first_result = app.run_live_status_bot_check(
            store=store,
            send_messages=True,
            write_changes=True,
        )

        self.assertEqual(first_result["notifications"], [])
        self.assertEqual(row["pending_design_fingerprint"], design_marker("design-v2"))
        self.assertEqual(row["last_design_fingerprint"], design_marker())
        send_event.assert_not_called()

        second_result = app.run_live_status_bot_check(
            store=store,
            send_messages=True,
            write_changes=True,
        )

        self.assertEqual(second_result["notifications"][0]["event"], "app_updated")
        self.assertEqual(second_result["notifications"][0]["changes"], ["design"])
        self.assertEqual(row["last_design_fingerprint"], design_marker("design-v2"))
        self.assertEqual(row["pending_design_fingerprint"], "")
        self.assertIn("changes=design", store.logs[0][3])
        send_event.assert_called_once()
        self.assertEqual(fetch_state.call_count, 4)
        summarize.assert_not_called()

    @patch("app.send_telegram_event_message")
    @patch("app.fetch_google_play_update_state")
    @patch("app.summarize_google_availability")
    @patch("app.probe_google_play_live_status")
    def test_unconfirmed_design_change_does_not_alert_or_replace_baseline(
        self,
        probe,
        summarize,
        fetch_state,
        send_event,
    ):
        probe.return_value = {"state": "live", "country": "US", "error": ""}
        fetch_state.side_effect = [
            update_state(fingerprint="design-transient"),
            update_state(fingerprint="design-v1"),
        ]
        row = app_row()
        row["last_store_version"] = release_marker()
        row["last_design_fingerprint"] = design_marker()
        store = FakeStore([row])

        result = app.run_live_status_bot_check(store=store, send_messages=True, write_changes=True)

        self.assertEqual(result["notifications"], [])
        self.assertEqual(result["metadata_design_unconfirmed"], 1)
        self.assertNotIn("last_design_fingerprint", store.updates[0][1])
        self.assertEqual(store.updates[0][1]["pending_design_fingerprint"], "")
        self.assertEqual(store.logs, [])
        send_event.assert_not_called()
        summarize.assert_not_called()

    @patch("app.send_telegram_event_message")
    @patch("app.fetch_google_play_update_state")
    @patch("app.summarize_google_availability")
    @patch("app.probe_google_play_live_status")
    def test_unconfirmed_release_change_does_not_alert_or_replace_baseline(
        self,
        probe,
        summarize,
        fetch_state,
        send_event,
    ):
        probe.return_value = {"state": "live", "country": "US", "error": ""}
        fetch_state.side_effect = [
            update_state(version="1.1.0"),
            update_state(version="1.0.0"),
        ]
        row = app_row()
        row["last_store_version"] = release_marker()
        row["last_design_fingerprint"] = design_marker()
        store = FakeStore([row])

        result = app.run_live_status_bot_check(store=store, send_messages=True, write_changes=True)

        self.assertEqual(result["notifications"], [])
        self.assertEqual(result["metadata_release_unconfirmed"], 1)
        self.assertNotIn("last_store_version", store.updates[0][1])
        self.assertEqual(store.updates[0][1]["pending_store_version"], "")
        self.assertEqual(store.logs, [])
        self.assertEqual(fetch_state.call_count, 2)
        send_event.assert_not_called()
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
        row["last_store_version"] = release_marker()
        row["last_design_fingerprint"] = design_marker()
        row["pending_store_version"] = release_marker(version="1.1.0")
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
