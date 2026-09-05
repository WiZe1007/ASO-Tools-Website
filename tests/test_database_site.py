import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import database_site.app as database_app


class FakeStore:
    apps = []
    logs = []
    s_apps = []
    s_logs = []

    def __init__(self, database_key="wwa", **_kwargs):
        self.database_key = database_key

    def _apps(self):
        return self.s_apps if self.database_key == "s" else self.apps

    def _logs(self):
        return self.s_logs if self.database_key == "s" else self.logs

    def load_all_apps(self):
        return [dict(item) for item in self._apps()]

    def append_app(self, item):
        created = dict(item)
        apps = self._apps()
        created["row_index"] = max([int(app.get("row_index", 1)) for app in apps] + [1]) + 1
        apps.append(created)

    def update_app(self, row_index, current, updates):
        apps = self._apps()
        for index, item in enumerate(apps):
            if int(item.get("row_index") or 0) == row_index:
                changed = dict(item)
                changed.update(updates)
                apps[index] = changed
                return

    def append_log(self, event, app_data, details):
        self._logs().append((event, dict(app_data), details))

    def delete_app(self, row_index):
        apps = self._apps()
        apps[:] = [item for item in apps if item["row_index"] != row_index]


class FakeUserStore:
    def __init__(self):
        self.users = {}

    def get_user(self, identifier):
        user = self.users.get(database_app.normalize_email(identifier))
        return dict(user) if user else None

    def create_user(self, email, password_hash, *, database_access=None):
        normalized = database_app.normalize_email(email)
        if normalized in self.users:
            raise ValueError("USER_ALREADY_EXISTS")
        user = {
            "id": normalized,
            "email": normalized,
            "password_hash": password_hash,
            "active": 1,
            "created_at": database_app.utc_now_iso(),
            "last_login_at": "",
            "database_access": database_access,
        }
        self.users[normalized] = user
        return dict(user)

    def update_last_login(self, email, last_login_at):
        self.users[database_app.normalize_email(email)]["last_login_at"] = last_login_at

    def load_users(self):
        return [dict(user) for user in self.users.values()]

    def delete_user(self, email):
        normalized = database_app.normalize_email(email)
        if normalized not in self.users:
            raise ValueError("USER_NOT_FOUND")
        del self.users[normalized]

    def update_user(self, email, *, password_hash=None, active=None, database_access=None):
        normalized = database_app.normalize_email(email)
        if normalized not in self.users:
            raise ValueError("USER_NOT_FOUND")
        if password_hash is not None:
            self.users[normalized]["password_hash"] = password_hash
        if active is not None:
            self.users[normalized]["active"] = active
        if database_access is not None:
            self.users[normalized]["database_access"] = database_access
        return dict(self.users[normalized])


class DatabaseSiteTests(unittest.TestCase):
    def setUp(self):
        self.original_auth_required = database_app.AUTH_REQUIRED
        self.original_store = database_app.GoogleSheetsStore
        self.original_s_allowed_emails = database_app.S_ALLOWED_EMAILS
        self.original_s_spreadsheet_id = database_app.S_SPREADSHEET_ID
        database_app.AUTH_REQUIRED = False
        database_app.GoogleSheetsStore = FakeStore
        database_app.S_ALLOWED_EMAILS = ""
        database_app.S_SPREADSHEET_ID = "s-test-sheet"
        FakeStore.apps = []
        FakeStore.logs = []
        FakeStore.s_apps = []
        FakeStore.s_logs = []
        database_app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = database_app.app.test_client()
        with self.client.session_transaction() as session:
            session["csrf_token"] = "test-csrf"
            session["user_email"] = "editor@wildwildgroup.com"

    def tearDown(self):
        database_app.AUTH_REQUIRED = self.original_auth_required
        database_app.GoogleSheetsStore = self.original_store
        database_app.S_ALLOWED_EMAILS = self.original_s_allowed_emails
        database_app.S_SPREADSHEET_ID = self.original_s_spreadsheet_id

    def headers(self):
        return {"X-CSRF-Token": "test-csrf"}

    def test_google_sheets_schema_adds_pending_update_columns(self):
        store = object.__new__(self.original_store)
        store.apps_sheet = "Apps"
        store.log_sheet = "Checks"
        writes = []
        store.get_sheet_titles = lambda: {"Apps", "Checks"}
        store.add_sheet = lambda _title: self.fail("Existing sheets must not be recreated")
        store.get_values = lambda _sheet, cell_range: (
            [database_app.PREVIOUS_APPS_SHEET_HEADERS]
            if cell_range == "A1:R1"
            else [database_app.CHECKS_SHEET_HEADERS]
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

    def test_normalize_package_input(self):
        self.assertEqual(
            database_app.normalize_package_input(
                "https://play.google.com/store/apps/details?id=com.example.game&gl=US"
            ),
            "com.example.game",
        )
        self.assertEqual(database_app.normalize_package_input("com.example.game"), "com.example.game")
        self.assertEqual(database_app.normalize_package_input("not a package"), "")

    def test_dashboard_uses_database_favicon(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"/static/db/data-base-favicon.png?v=20260804", response.data)

    def test_add_app_and_write_audit_log(self):
        response = self.client.post(
            "/api/apps",
            json={"app_input": "com.example.game", "app_name": "Example Game", "status": "watch"},
            headers=self.headers(),
        )
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["app"]["app_id"], "com.example.game")
        self.assertEqual(payload["app"]["owner"], "editor@wildwildgroup.com")
        self.assertEqual(payload["app"]["app_type"], "full")
        self.assertEqual(payload["app"]["app_type_label"], "Повноцінна")
        self.assertEqual(FakeStore.logs[0][0], "database_add")

    def test_add_placeholder_app(self):
        response = self.client.post(
            "/api/apps",
            json={"app_input": "com.example.placeholder", "app_type": "placeholder"},
            headers=self.headers(),
        )
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()["app"]
        self.assertEqual(payload["app_type"], "placeholder")
        self.assertEqual(payload["app_type_label"], "Заглушка")
        self.assertEqual(FakeStore.apps[0]["app_type"], "placeholder")

    def test_invalid_app_type_is_rejected(self):
        response = self.client.post(
            "/api/apps",
            json={"app_input": "com.example.invalid", "app_type": "demo"},
            headers=self.headers(),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["error"], "INVALID_APP_TYPE")

    def test_duplicate_app_is_rejected(self):
        FakeStore.apps = [{
            "row_index": 2,
            "enabled": True,
            "status": "watch",
            "app_url": database_app.google_play_url("com.example.game"),
            "app_id": "com.example.game",
            "app_name": "Example",
        }]
        response = self.client.post(
            "/api/apps",
            json={"app_input": "com.example.game"},
            headers=self.headers(),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "DUPLICATE_APP")

    def test_update_checks_expected_app_id(self):
        FakeStore.apps = [{
            "row_index": 2,
            "enabled": True,
            "status": "watch",
            "app_url": database_app.google_play_url("com.example.game"),
            "app_id": "com.example.game",
            "app_name": "Example",
        }]
        stale = self.client.patch(
            "/api/apps/2",
            json={"expected_app_id": "com.other.game", "status": "live"},
            headers=self.headers(),
        )
        self.assertEqual(stale.status_code, 409)

        updated = self.client.patch(
            "/api/apps/2",
            json={"expected_app_id": "com.example.game", "status": "live", "notes": "Ready", "app_type": "placeholder"},
            headers=self.headers(),
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["app"]["status"], "live")
        self.assertEqual(FakeStore.apps[0]["notes"], "Ready")
        self.assertEqual(FakeStore.apps[0]["app_type"], "placeholder")

    def test_mutation_requires_csrf(self):
        response = self.client.post("/api/apps", json={"app_input": "com.example.game"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "CSRF_FAILED")

    def seed_app(self, app_id="com.example.old", row_index=2):
        item = {field: "previous-value" for field in database_app.MONITORING_FIELDS}
        item.update({
            "row_index": row_index, "app_id": app_id, "app_url": database_app.google_play_url(app_id),
            "app_name": app_id, "status": "live", "enabled": True,
            "owner": "editor@wildwildgroup.com", "notes": "Keep notes", "app_type": "placeholder",
        })
        FakeStore.apps.append(item)
        return item

    def test_package_change_resets_monitoring_and_updates_url(self):
        original = self.seed_app()
        response = self.client.patch("/api/apps/2", headers=self.headers(), json={
            "expected_app_id": original["app_id"],
            "app_input": "https://play.google.com/store/apps/details?id=com.example.new&gl=UA",
            "status": "live",
        })
        self.assertEqual(response.status_code, 200)
        saved = FakeStore.apps[0]
        self.assertEqual(saved["app_id"], "com.example.new")
        self.assertEqual(saved["app_name"], "com.example.new")
        self.assertEqual(saved["app_url"], database_app.google_play_url("com.example.new"))
        self.assertEqual(saved["status"], "watch")
        for field in database_app.MONITORING_FIELDS:
            self.assertEqual(saved[field], "", field)
        for field in ("owner", "notes", "enabled", "app_type"):
            self.assertEqual(saved[field], original[field])
        self.assertEqual(json.loads(FakeStore.logs[0][2])["previous_app_id"], original["app_id"])

    def test_unchanged_package_preserves_monitoring(self):
        original = dict(self.seed_app())
        response = self.client.patch("/api/apps/2", headers=self.headers(), json={
            "expected_app_id": original["app_id"], "app_input": original["app_url"], "app_name": "New title",
        })
        self.assertEqual(response.status_code, 200)
        for field in database_app.MONITORING_FIELDS + ["status"]:
            self.assertEqual(FakeStore.apps[0][field], original[field])

    def test_package_change_rejects_invalid_duplicate_and_stale_requests(self):
        self.seed_app()
        self.seed_app("com.example.duplicate", 4)
        before = [dict(item) for item in FakeStore.apps]
        for payload, code in (
            ({"expected_app_id": "com.example.old", "app_input": "not a package"}, "INVALID_APP_ID"),
            ({"expected_app_id": "com.example.old", "app_id": "com.example.duplicate"}, "DUPLICATE_APP"),
            ({"expected_app_id": "com.example.stale", "app_id": "com.example.new"}, "STALE_ROW"),
            ({"app_id": "com.example.new"}, "EXPECTED_APP_ID_REQUIRED"),
        ):
            with self.subTest(code=code):
                response = self.client.patch("/api/apps/2", headers=self.headers(), json=payload)
                self.assertEqual(response.get_json()["error"], code)
                self.assertEqual(FakeStore.apps, before)
        self.assertEqual(FakeStore.logs, [])

    def test_delete_removes_record_preserves_other_rows_and_allows_readding(self):
        original = self.seed_app()
        neighbour = self.seed_app("com.example.neighbour", 5)
        response = self.client.delete("/api/apps/2", headers=self.headers(), json={"expected_app_id": original["app_id"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(FakeStore.apps, [neighbour])
        self.assertEqual(FakeStore.logs[0][0], "database_delete")
        self.assertEqual(json.loads(FakeStore.logs[0][2])["actor"], "editor@wildwildgroup.com")
        listed = self.client.get("/api/apps").get_json()["apps"]
        self.assertEqual([item["app_id"] for item in listed], [neighbour["app_id"]])
        added = self.client.post("/api/apps", headers=self.headers(), json={"app_input": original["app_id"]})
        self.assertEqual(added.status_code, 201)

    def test_delete_checks_csrf_identity_missing_rows_and_scope(self):
        original = dict(self.seed_app())
        cases = [
            ("/api/apps/2", {}, {"expected_app_id": original["app_id"]}, 403, "CSRF_FAILED"),
            ("/api/apps/2", self.headers(), {}, 422, "EXPECTED_APP_ID_REQUIRED"),
            ("/api/apps/2", self.headers(), {"expected_app_id": "com.example.stale"}, 409, "STALE_ROW"),
            ("/api/apps/1", self.headers(), {"expected_app_id": original["app_id"]}, 422, "INVALID_ROW"),
            ("/api/apps/7", self.headers(), {"expected_app_id": original["app_id"]}, 404, "APP_NOT_FOUND"),
            ("/api/databases/s/apps/2", self.headers(), {"expected_app_id": original["app_id"]}, 403, "DATABASE_FORBIDDEN"),
        ]
        for path, headers, payload, status, code in cases:
            with self.subTest(code=code):
                response = self.client.delete(path, headers=headers, json=payload)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.get_json()["error"], code)
                self.assertEqual(FakeStore.apps, [original])
        self.assertEqual(FakeStore.logs, [])

    def test_delete_and_rename_are_scoped_to_s_database(self):
        original = self.seed_app()
        FakeStore.s_apps = [dict(original)]
        database_app.S_ALLOWED_EMAILS = "editor@wildwildgroup.com"
        renamed = self.client.patch("/api/databases/s/apps/2", headers=self.headers(), json={
            "expected_app_id": original["app_id"], "app_id": "com.example.second",
        })
        self.assertEqual(renamed.status_code, 200)
        deleted = self.client.delete("/api/databases/s/apps/2", headers=self.headers(), json={
            "expected_app_id": "com.example.second",
        })
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(FakeStore.apps, [original])
        self.assertEqual(FakeStore.s_apps, [])
        self.assertEqual(FakeStore.logs, [])

    def test_sheet_delete_clears_only_target_record_without_row_shift(self):
        store = self.original_store(apps_sheet="Apps", spreadsheet_id="test")
        with patch.object(store, "_request") as request:
            store.delete_app(3)
        request.assert_called_once_with("POST", "/values:batchClear", json={"ranges": ["'Apps'!A3:R3"]})

    def test_delete_storage_failure_keeps_record_and_can_retry(self):
        original = dict(self.seed_app())
        payload = {"expected_app_id": original["app_id"]}
        with patch.object(FakeStore, "delete_app", side_effect=database_app.DatabaseConfigError("Temporary failure")):
            response = self.client.delete("/api/apps/2", headers=self.headers(), json=payload)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(FakeStore.apps, [original])
        self.assertEqual(FakeStore.logs, [])
        self.assertEqual(self.client.delete("/api/apps/2", headers=self.headers(), json=payload).status_code, 200)

    def test_edit_and_delete_reject_non_object_payloads(self):
        original = dict(self.seed_app())
        for method in (self.client.patch, self.client.delete):
            response = method("/api/apps/2", headers=self.headers(), json=["invalid"])
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.get_json()["error"], "INVALID_PAYLOAD")
        self.assertEqual(FakeStore.apps, [original])

    def test_sheet_edit_writes_only_changed_cells(self):
        store = self.original_store(apps_sheet="Apps", spreadsheet_id="test")
        with patch.object(store, "_request") as request:
            store.update_app(2, self.seed_app(), {"notes": "Changed"})
        request.assert_called_once_with("POST", "/values:batchUpdate", json={
            "valueInputOption": "RAW", "data": [{"range": "'Apps'!G2", "values": [["Changed"]]}],
        })

    def test_wwa_database_is_available_to_every_corporate_user(self):
        response = self.client.get("/api/databases/wwa/apps")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["database"], "wwa")

    def test_s_database_is_hidden_and_forbidden_without_allowlist(self):
        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotIn(b'data-database-key="s"', dashboard.data)

        response = self.client.get("/api/databases/s/apps")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "DATABASE_FORBIDDEN")

    def test_allowlisted_user_can_use_separate_s_database(self):
        database_app.S_ALLOWED_EMAILS = "editor@wildwildgroup.com"
        dashboard = self.client.get("/")
        self.assertIn(b'data-database-key="s"', dashboard.data)

        response = self.client.post(
            "/api/databases/s/apps",
            json={"app_input": "com.example.second", "app_name": "Second Team App"},
            headers=self.headers(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["database"], "s")
        self.assertEqual(len(FakeStore.s_apps), 1)
        self.assertEqual(len(FakeStore.apps), 0)
        self.assertIn('"database": "s"', FakeStore.s_logs[0][2])


class DatabaseSiteAuthTests(unittest.TestCase):
    def setUp(self):
        self.original_auth_required = database_app.AUTH_REQUIRED
        self.original_auth_db_path = database_app.AUTH_DB_PATH
        self.original_auth_storage = database_app.AUTH_STORAGE
        self.original_auth_user_store = database_app.AUTH_USER_STORE
        self.original_allowed_domain = database_app.AUTH_ALLOWED_EMAIL_DOMAIN
        self.temp_directory = tempfile.TemporaryDirectory()
        database_app.AUTH_REQUIRED = True
        database_app.AUTH_STORAGE = "sqlite"
        database_app.AUTH_USER_STORE = None
        database_app.AUTH_USER_CACHE.clear()
        database_app.AUTH_DB_PATH = Path(self.temp_directory.name) / "users.sqlite"
        database_app.AUTH_ALLOWED_EMAIL_DOMAIN = "@wildwildgroup.com"
        database_app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = database_app.app.test_client()

    def tearDown(self):
        database_app.AUTH_REQUIRED = self.original_auth_required
        database_app.AUTH_DB_PATH = self.original_auth_db_path
        database_app.AUTH_STORAGE = self.original_auth_storage
        database_app.AUTH_USER_STORE = self.original_auth_user_store
        database_app.AUTH_USER_CACHE.clear()
        database_app.AUTH_ALLOWED_EMAIL_DOMAIN = self.original_allowed_domain
        self.temp_directory.cleanup()

    def test_dashboard_requires_login(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_delete_requires_login(self):
        response = self.client.delete("/api/apps/2", json={"expected_app_id": "com.example.game"})
        self.assertEqual(response.status_code, 401)

    def test_login_uses_database_favicon(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"/static/db/data-base-favicon.png?v=20260804", response.data)

    def test_registration_is_removed(self):
        response = self.client.post(
            "/register",
            data={
                "email": "employee@example.com",
                "password": "correct-password",
                "password_confirm": "correct-password",
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(database_app.get_user_by_email("employee@example.com"))

    def test_manually_created_corporate_user_can_logout_and_login(self):
        email = "employee@wildwildgroup.com"
        database_app.create_user(email, "correct-password")
        self.client.get("/login")
        with self.client.session_transaction() as session:
            csrf = session["csrf_token"]
        self.client.post("/login", data={"email": email, "password": "correct-password", "csrf_token": csrf})
        self.assertIsNotNone(database_app.get_user_by_email(email))

        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(email.encode(), dashboard.data)

        with self.client.session_transaction() as session:
            csrf = session["csrf_token"]
        logged_out = self.client.post("/logout", data={"csrf_token": csrf})
        self.assertEqual(logged_out.status_code, 302)
        self.assertIn("/login", logged_out.headers["Location"])

        self.client.get("/login")
        with self.client.session_transaction() as session:
            csrf = session["csrf_token"]
        logged_in = self.client.post(
            "/login",
            data={"email": email, "password": "correct-password", "csrf_token": csrf},
        )
        self.assertEqual(logged_in.status_code, 302)
        self.assertEqual(logged_in.headers["Location"], "/")


class DatabaseSiteGoogleSheetsAuthTests(unittest.TestCase):
    def setUp(self):
        self.original_auth_required = database_app.AUTH_REQUIRED
        self.original_auth_storage = database_app.AUTH_STORAGE
        self.original_auth_user_store = database_app.AUTH_USER_STORE
        self.original_allowed_domain = database_app.AUTH_ALLOWED_EMAIL_DOMAIN
        self.user_store = FakeUserStore()
        database_app.AUTH_REQUIRED = True
        database_app.AUTH_STORAGE = "google_sheets"
        database_app.AUTH_USER_STORE = self.user_store
        database_app.AUTH_USER_CACHE.clear()
        database_app.AUTH_ALLOWED_EMAIL_DOMAIN = "@wildwildgroup.com"
        database_app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = database_app.app.test_client()

    def tearDown(self):
        database_app.AUTH_REQUIRED = self.original_auth_required
        database_app.AUTH_STORAGE = self.original_auth_storage
        database_app.AUTH_USER_STORE = self.original_auth_user_store
        database_app.AUTH_USER_CACHE.clear()
        database_app.AUTH_ALLOWED_EMAIL_DOMAIN = self.original_allowed_domain

    def test_google_sheets_user_survives_cache_clear_and_can_login(self):
        email = "free-user@wildwildgroup.com"
        database_app.create_user(email, "correct-password")
        self.assertIn(email, self.user_store.users)
        self.assertNotEqual(self.user_store.users[email]["password_hash"], "correct-password")

        database_app.AUTH_USER_CACHE.clear()
        self.client.get("/login")
        with self.client.session_transaction() as session:
            csrf = session["csrf_token"]
        logged_in = self.client.post(
            "/login",
            data={"email": email, "password": "correct-password", "csrf_token": csrf},
        )
        self.assertEqual(logged_in.status_code, 302)
        self.assertEqual(logged_in.headers["Location"], "/")
        self.assertTrue(self.user_store.users[email]["last_login_at"])


if __name__ == "__main__":
    unittest.main()
