import tempfile
import unittest
from pathlib import Path

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


class FakeUserStore:
    def __init__(self):
        self.users = {}

    def get_user(self, identifier):
        user = self.users.get(database_app.normalize_email(identifier))
        return dict(user) if user else None

    def create_user(self, email, password_hash):
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
        }
        self.users[normalized] = user
        return dict(user)

    def update_last_login(self, email, last_login_at):
        self.users[database_app.normalize_email(email)]["last_login_at"] = last_login_at


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

    def test_login_uses_database_favicon(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"/static/db/data-base-favicon.png?v=20260804", response.data)

    def test_registration_rejects_external_email_domain(self):
        response = self.client.post(
            "/register",
            data={
                "email": "employee@example.com",
                "password": "correct-password",
                "password_confirm": "correct-password",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Реєстрація доступна тільки".encode(), response.data)
        self.assertIsNone(database_app.get_user_by_email("employee@example.com"))

    def test_corporate_user_can_register_logout_and_login(self):
        email = "employee@wildwildgroup.com"
        registered = self.client.post(
            "/register",
            data={
                "email": email,
                "password": "correct-password",
                "password_confirm": "correct-password",
            },
        )
        self.assertEqual(registered.status_code, 302)
        self.assertEqual(registered.headers["Location"], "/")
        self.assertIsNotNone(database_app.get_user_by_email(email))

        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(email.encode(), dashboard.data)

        logged_out = self.client.get("/logout")
        self.assertEqual(logged_out.status_code, 302)
        self.assertIn("/login", logged_out.headers["Location"])

        logged_in = self.client.post(
            "/login",
            data={"email": email, "password": "correct-password"},
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
        registered = self.client.post(
            "/register",
            data={
                "email": email,
                "password": "correct-password",
                "password_confirm": "correct-password",
            },
        )
        self.assertEqual(registered.status_code, 302)
        self.assertIn(email, self.user_store.users)
        self.assertNotEqual(self.user_store.users[email]["password_hash"], "correct-password")

        self.client.get("/logout")
        database_app.AUTH_USER_CACHE.clear()
        logged_in = self.client.post(
            "/login",
            data={"email": email, "password": "correct-password"},
        )
        self.assertEqual(logged_in.status_code, 302)
        self.assertEqual(logged_in.headers["Location"], "/")
        self.assertTrue(self.user_store.users[email]["last_login_at"])


if __name__ == "__main__":
    unittest.main()
