import tempfile
import sqlite3
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import Mock, patch

from werkzeug.security import check_password_hash, generate_password_hash

import app as tools_app
import database_site.app as database_app
from account_access import TEAM_EMAILS, account_database_access
from tests.test_database_site import FakeStore, FakeUserStore


class SharedAccountTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.store = FakeUserStore()
        self.stack.enter_context(patch.object(database_app, "AUTH_STORAGE", "google_sheets"))
        self.stack.enter_context(patch.object(database_app, "AUTH_USER_STORE", self.store))
        self.stack.enter_context(patch.object(database_app, "S_ALLOWED_EMAILS", ""))
        self.stack.enter_context(patch.object(tools_app, "AUTH_STORAGE", "google_sheets"))
        self.stack.enter_context(patch.object(tools_app, "S_LIVE_DB_ALLOWED_EMAILS", ""))
        for module in (database_app, tools_app):
            self.stack.enter_context(patch.object(module, "AUTH_REQUIRED", True))
            self.stack.enter_context(patch.object(module, "AUTH_ALLOWED_EMAIL_DOMAIN", "@wildwildgroup.com"))
            self.stack.enter_context(patch.dict(module.app.config, {
                "TESTING": True, "SESSION_COOKIE_SECURE": False,
                "SECRET_KEY": "test-" + module.__name__,
                "AUTH_ADMIN_EMAILS": "admin@wildwildgroup.com",
            }))
        database_app.AUTH_USER_CACHE.clear()
        self.addCleanup(database_app.AUTH_USER_CACHE.clear)
        self.password = "Test-password-only-42"
        self.admin = "admin@wildwildgroup.com"
        self.employee = "employee@wildwildgroup.com"
        password_hash = generate_password_hash(self.password)
        for email in (self.admin, self.employee):
            self.store.create_user(email, password_hash)
        self.clients = [module.app.test_client() for module in (database_app, tools_app)]

    def csrf(self, client):
        with client.session_transaction() as session:
            return session["csrf_token"]

    def login(self, client, email=None, password=None, **extra):
        client.get("/login")
        return client.post("/login", data={
            "email": email or self.employee, "password": password or self.password,
            "csrf_token": self.csrf(client), **extra,
        })

    def admin_post(self, client, path="/admin/users", **data):
        return client.post(path, data={"csrf_token": self.csrf(client), **data})

    def test_registration_removed_on_both_sites_even_for_signed_in_admin(self):
        for client in self.clients:
            for signed_in in (False, True):
                if signed_in:
                    self.login(client, self.admin)
                for method in (client.get, client.post):
                    self.assertEqual(method("/register").status_code, 404)
            response = client.get("/login")
            self.assertNotIn(b"/register", response.data)
            self.assertNotIn(b"password_confirm", response.data)

    def test_all_five_existing_accounts_keep_passwords_and_work_on_both_sites(self):
        password_hash = generate_password_hash("Existing-team-password")
        for email in TEAM_EMAILS:
            self.store.create_user(email, password_hash)
        for client in self.clients:
            for email in TEAM_EMAILS:
                with self.subTest(email=email):
                    self.assertEqual(self.login(client, email, "Existing-team-password").status_code, 302)
                    self.assertEqual(client.get("/").status_code, 200)
                    self.assertEqual(self.store.users[email]["password_hash"], password_hash)
                    client.get("/logout")
        result = tools_app.app.test_cli_runner().invoke(args=["users", "check-team"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn(password_hash, result.output)

    def test_anonymous_and_regular_users_cannot_read_or_create_accounts(self):
        for client in self.clients:
            self.assertEqual(client.get("/admin/users").status_code, 302)
            self.assertEqual(self.login(client).status_code, 302)
            self.assertEqual(client.get("/admin/users").status_code, 403)
            result = self.admin_post(client, email="intruder@wildwildgroup.com", password=self.password)
            self.assertEqual(result.status_code, 403)
            self.assertNotIn(b"/admin/users", client.get("/").data)
        self.assertNotIn("intruder@wildwildgroup.com", self.store.users)

    def test_admin_can_add_user_from_either_site_without_changing_own_session(self):
        for index, client in enumerate(self.clients):
            self.login(client, self.admin)
            email = f"new{index}@wildwildgroup.com"
            result = self.admin_post(client, email=email.upper(), password=self.password)
            self.assertEqual(result.status_code, 303)
            self.assertEqual(result.headers["Location"], "/admin/users")
            with client.session_transaction() as session:
                self.assertEqual(session["user_email"], self.admin)
            self.assertTrue(check_password_hash(self.store.users[email]["password_hash"], self.password))
            for module in (tools_app, database_app):
                self.assertEqual(self.login(module.app.test_client(), email).status_code, 302)

    def test_admin_page_does_not_expose_passwords_or_hashes(self):
        for client in self.clients:
            self.login(client, self.admin)
            response = client.get("/admin/users")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertIn(self.employee.encode(), response.data)
            self.assertNotIn(self.password.encode(), response.data)
            self.assertNotIn(self.store.users[self.admin]["password_hash"].encode(), response.data)

    def test_create_assigns_explicit_wwa_s_both_or_no_access(self):
        for index, client in enumerate(self.clients):
            self.login(client, self.admin)
            for scope in ([], ["wwa"], ["s"], ["wwa", "s"]):
                email = f"new{index}.{len(scope)}.{scope[0] if scope else 'none'}@wildwildgroup.com"
                response = self.admin_post(client, email=email, password=self.password, database_access=scope)
                self.assertEqual(response.status_code, 303)
                self.assertEqual(account_database_access(self.store.users[email], email), set(scope))

    def test_access_edits_from_both_sites_enforce_database_isolation(self):
        for admin_client in self.clients:
            self.login(admin_client, self.admin)
            for scopes in (["wwa"], ["s"], ["wwa", "s"], []):
                response = self.admin_post(admin_client, f"/admin/users/{self.employee}",
                                           action="access", database_access=scopes)
                self.assertEqual(response.status_code, 303)
                db_client = database_app.app.test_client()
                tools_client = tools_app.app.test_client()
                self.login(db_client)
                self.login(tools_client)
                fake = Mock()
                fake.load_all_apps.return_value = []
                with patch.object(database_app, "build_store", return_value=fake) as build:
                    for key in ("wwa", "s"):
                        build.reset_mock()
                        response = db_client.get(f"/api/databases/{key}/apps")
                        self.assertEqual(response.status_code, 200 if key in scopes else 403)
                        if key not in scopes:
                            build.assert_not_called()
                            for method, suffix in ((db_client.post, ""), (db_client.patch, "/2")):
                                response = method(f"/api/databases/{key}/apps{suffix}", json={},
                                                  headers={"X-CSRF-Token": self.csrf(db_client)})
                                self.assertEqual(response.status_code, 403)
                            build.assert_not_called()
                    self.assertEqual(db_client.get("/api/apps").status_code, 200 if "wwa" in scopes else 403)
                dashboard = db_client.get("/")
                self.assertEqual(dashboard.status_code, 200 if scopes else 403)
                self.assertEqual(db_client.get("/admin/users").status_code, 403)
                with patch.object(tools_app, "build_live_apps_database_payload", return_value={}) as payload, \
                     patch.object(tools_app, "build_s_live_apps_store", return_value=Mock()):
                    for key, path in (("wwa", "live-apps"), ("s", "s-live-apps")):
                        payload.reset_mock()
                        self.assertEqual(tools_client.get(f"/{path}").status_code, 200 if key in scopes else 403)
                        self.assertEqual(tools_client.get(f"/api/{path}").status_code, 200 if key in scopes else 403)
                        if key not in scopes:
                            payload.assert_not_called()
                    nav = tools_client.get("/").data
                    self.assertEqual(b'href="/live-apps"' in nav, "wwa" in scopes)
                    self.assertEqual(b'href="/s-live-apps"' in nav, "s" in scopes)

    def test_access_revocation_on_another_worker_bypasses_cached_permissions(self):
        self.store.update_user(self.employee, database_access="s,wwa")
        for client in self.clients:
            self.login(client)
        self.assertEqual(database_app.AUTH_USER_CACHE[self.employee][1]["database_access"], "s,wwa")
        self.store.update_user(self.employee, database_access="none")
        self.assertEqual(self.clients[0].get("/api/apps").status_code, 403)
        self.assertEqual(self.clients[0].get("/api/databases/s/apps").status_code, 403)
        database_app.cache_user(self.employee, {**self.store.get_user(self.employee), "database_access": "s,wwa"})
        self.assertEqual(self.clients[1].get("/api/live-apps").status_code, 403)
        self.assertEqual(self.clients[1].get("/api/s-live-apps").status_code, 403)

    def test_s_only_user_can_create_and_edit_s_apps_without_touching_wwa(self):
        self.store.update_user(self.employee, database_access="s")
        client = self.clients[0]
        self.login(client)
        with patch.object(FakeStore, "apps", []), patch.object(FakeStore, "s_apps", []), \
             patch.object(FakeStore, "logs", []), patch.object(FakeStore, "s_logs", []), \
             patch.object(database_app, "GoogleSheetsStore", FakeStore):
            headers = {"X-CSRF-Token": self.csrf(client)}
            response = client.post("/api/databases/s/apps", json={"app_input": "com.test.app", "app_type": "full"}, headers=headers)
            self.assertEqual(response.status_code, 201)
            response = client.patch("/api/databases/s/apps/2", json={"expected_app_id": "com.test.app", "notes": "S only"}, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(FakeStore.s_apps[0]["notes"], "S only")
            self.assertEqual(FakeStore.apps, [])
            self.assertEqual(FakeStore.logs, [])
            self.assertEqual(len(FakeStore.s_logs), 2)

    def test_admin_without_database_permissions_can_still_manage_accounts(self):
        self.store.update_user(self.admin, database_access="none")
        for client in self.clients:
            self.login(client, self.admin)
            self.assertEqual(client.get("/admin/users").status_code, 200)
        response = self.clients[0].get("/")
        self.assertEqual(response.status_code, 403)
        self.assertIn(b'href="/admin/users"', response.data)

    def test_legacy_rights_are_preserved_but_explicit_choices_override_environment(self):
        user = self.store.get_user(self.employee)
        self.assertEqual(account_database_access(user), {"wwa"})
        self.assertEqual(account_database_access(user, self.employee), {"wwa", "s"})
        for stored, expected in (("wwa", {"wwa"}), ("s", {"s"}), ("none", set()), ("invalid", set())):
            self.assertEqual(account_database_access({**user, "database_access": stored}, self.employee), expected)

    def test_group_lists_show_only_assigned_users_and_default_new_user_to_group(self):
        self.store.update_user(self.employee, database_access="s")
        for client in self.clients:
            self.login(client, self.admin)
            response = client.get("/admin/users?group=wwa")
            self.assertIn(self.admin.encode(), response.data)
            self.assertNotIn(self.employee.encode(), response.data)
            response = client.get("/admin/users?group=s")
            self.assertIn(self.employee.encode(), response.data)
            self.assertNotIn(f'data-delete-email="{self.admin}"'.encode(), response.data)
            self.assertIn(b'value="s" checked', response.data)

    def test_access_mutations_require_admin_csrf_and_known_database_keys(self):
        for client in self.clients:
            self.login(client)
            path = f"/admin/users/{self.employee}"
            self.assertEqual(self.admin_post(client, path, action="access", database_access=["s"]).status_code, 403)
            self.login(client, self.admin)
            self.assertEqual(client.post(path, data={"action": "access", "database_access": ["s"]}).status_code, 403)
            self.assertEqual(self.admin_post(client, path, action="access", database_access=["admin"]).status_code, 400)
            response = self.admin_post(client, email="blocked@wildwildgroup.com", password=self.password, database_access=["root"])
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("blocked@wildwildgroup.com", self.store.users)

    def test_duplicate_does_not_overwrite_existing_password(self):
        original = self.store.users[self.employee]["password_hash"]
        for client in self.clients:
            self.login(client, self.admin)
            result = self.admin_post(client, email=self.employee.upper(), password="different-password")
            self.assertEqual(result.status_code, 409)
            self.assertEqual(self.store.users[self.employee]["password_hash"], original)

    def test_delete_from_either_site_revokes_access_on_both_and_allows_recreation(self):
        for module in (database_app, tools_app):
            for client in self.clients:
                self.assertEqual(self.login(client).status_code, 302)
            admin_client = module.app.test_client()
            self.login(admin_client, self.admin)
            response = self.admin_post(admin_client, f"/admin/users/{self.employee}",
                                       action="delete", confirm_email=self.employee)
            self.assertEqual(response.status_code, 303)
            self.assertNotIn(self.employee, self.store.users)
            self.assertNotIn(self.employee.encode(), admin_client.get("/admin/users").data)
            self.assertIn(self.admin, self.store.users)
            self.assertEqual(admin_client.get("/admin/users").status_code, 200)
            for other in (database_app, tools_app):
                self.assertIsNone(other.get_user_by_email(self.employee))
                self.assertEqual(self.login(other.app.test_client()).status_code, 200)
            # Recreating the same email/password must not revive its old sessions.
            self.admin_post(admin_client, email=self.employee, password=self.password)
            for client in self.clients:
                self.assertEqual(client.get("/").status_code, 302)

    def test_disabled_account_can_be_permanently_deleted(self):
        for index, client in enumerate(self.clients):
            email = f"disabled{index}@wildwildgroup.com"
            self.store.create_user(email, generate_password_hash(self.password))
            self.store.update_user(email, active=0)
            self.login(client, self.admin)
            response = self.admin_post(client, f"/admin/users/{email}", action="delete", confirm_email=email)
            self.assertEqual(response.status_code, 303)
            self.assertNotIn(email, self.store.users)

    def test_delete_requires_admin_csrf_and_matching_confirmation(self):
        for client in self.clients:
            path = f"/admin/users/{self.employee}"
            self.assertEqual(client.post(path, data={"action": "delete", "confirm_email": self.employee}).status_code, 302)
            self.login(client)
            self.assertEqual(self.admin_post(client, path, action="delete", confirm_email=self.employee).status_code, 403)
            self.login(client, self.admin)
            for token in ("", "wrong-token"):
                response = client.post(path, data={"action": "delete", "confirm_email": self.employee, "csrf_token": token})
                self.assertEqual(response.status_code, 403)
            for confirmation in ("", self.admin):
                response = self.admin_post(client, path, action="delete", confirm_email=confirmation)
                self.assertEqual(response.status_code, 400)
            self.assertEqual(client.get(path).status_code, 405)
            self.assertIn(self.employee, self.store.users)

    def test_delete_self_is_blocked_and_button_is_not_rendered(self):
        for client in self.clients:
            self.login(client, self.admin)
            response = client.get("/admin/users")
            self.assertNotIn(f'data-delete-email="{self.admin}"'.encode(), response.data)
            self.assertIn(f'data-delete-email="{self.employee}"'.encode(), response.data)
            response = self.admin_post(client, f"/admin/users/{self.admin.upper()}", action="delete", confirm_email=self.admin)
            self.assertEqual(response.status_code, 400)
            self.assertIn(self.admin, self.store.users)

    def test_delete_missing_user_returns_not_found_without_changing_others(self):
        for client in self.clients:
            self.login(client, self.admin)
            email = "missing@wildwildgroup.com"
            response = self.admin_post(client, f"/admin/users/{email}", action="delete", confirm_email=email)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(len(self.store.users), 2)

    def test_delete_storage_failure_is_reported_and_invalidates_local_cache(self):
        for client in self.clients:
            self.login(client, self.admin)
            database_app.get_sheets_user(self.employee)
            with patch.object(self.store, "delete_user", side_effect=database_app.DatabaseConfigError("secret")):
                response = self.admin_post(client, f"/admin/users/{self.employee}", action="delete", confirm_email=self.employee)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(b"secret", response.data)
            self.assertNotIn(self.employee, database_app.AUTH_USER_CACHE)
            self.assertIn(self.employee, self.store.users)

    def test_deleted_admin_cannot_delete_others_even_with_cached_session(self):
        for client in self.clients:
            self.login(client, self.admin)
        del self.store.users[self.admin]
        for client in self.clients:
            response = self.admin_post(client, f"/admin/users/{self.employee}", action="delete", confirm_email=self.employee)
            self.assertIn(response.status_code, (302, 403))
            self.assertIn(self.employee, self.store.users)

    def test_login_and_admin_mutations_require_csrf(self):
        for client in self.clients:
            client.get("/login")
            result = client.post("/login", data={"email": self.admin, "password": self.password})
            self.assertEqual(result.status_code, 403)
            self.login(client, self.admin)
            for token in ("", "invalid"):
                result = client.post("/admin/users", data={
                    "email": "blocked@wildwildgroup.com", "password": self.password, "csrf_token": token,
                })
                self.assertEqual(result.status_code, 403)
                result = client.post(f"/admin/users/{self.employee}", data={
                    "action": "password", "password": "changed-password", "csrf_token": token,
                })
                self.assertEqual(result.status_code, 403)
        self.assertNotIn("blocked@wildwildgroup.com", self.store.users)

    def test_invalid_email_password_and_permission_fields_cannot_add_users(self):
        for client in self.clients:
            self.login(client, self.admin)
            for email, password in (
                ("outside@example.com", self.password), ("@wildwildgroup.com", self.password),
                ("a@@wildwildgroup.com", self.password), ("a@evilwildwildgroup.com", self.password),
                ("new@wildwildgroup.com", "short"), ("new@wildwildgroup.com", " " * 10),
                ("new@wildwildgroup.com", "p" * 257),
            ):
                self.assertEqual(self.admin_post(client, email=email, password=password).status_code, 400)
        self.assertEqual(len(self.store.users), 2)

    def test_regular_user_cannot_change_password_or_activate_an_account(self):
        for client in self.clients:
            self.login(client)
            for action in ("password", "status"):
                response = self.admin_post(client, f"/admin/users/{self.admin}",
                                           action=action, active="0", password="malicious-change")
                self.assertEqual(response.status_code, 403)

    def test_password_reset_revokes_existing_sessions_on_both_sites(self):
        for client in self.clients:
            self.login(client)
        admin_client = tools_app.app.test_client()
        self.login(admin_client, self.admin)
        result = self.admin_post(admin_client, f"/admin/users/{self.employee}",
                                 action="password", password="new-valid-password")
        self.assertEqual(result.status_code, 303)
        for client in self.clients:
            self.assertEqual(client.get("/").status_code, 302)
            self.assertEqual(self.login(client).status_code, 200)
            self.assertEqual(self.login(client, password="new-valid-password").status_code, 302)

    def test_disabled_user_cannot_log_in_even_with_stale_cached_active_user(self):
        for client in self.clients:
            self.login(client)
        self.store.users[self.employee]["active"] = 0
        for client in self.clients:
            self.assertEqual(self.login(client).status_code, 200)
            self.assertEqual(client.get("/").status_code, 302)

    def test_admin_can_disable_and_reactivate_but_not_disable_self(self):
        for client in self.clients:
            self.login(client, self.admin)
            for active in ("0", "1"):
                result = self.admin_post(client, f"/admin/users/{self.employee}", action="status", active=active)
                self.assertEqual(result.status_code, 303)
                self.assertEqual(self.store.users[self.employee]["active"], int(active))
            result = self.admin_post(client, f"/admin/users/{self.admin}", action="status", active="0")
            self.assertEqual(result.status_code, 400)

    def test_admin_recheck_ignores_cached_privileges(self):
        for client in self.clients:
            self.login(client, self.admin)
        self.store.users[self.admin]["active"] = 0
        for client in self.clients:
            self.assertIn(client.get("/admin/users").status_code, (302, 403))

    def test_storage_outage_fails_closed_without_local_password_fallback(self):
        for client in self.clients:
            self.login(client, self.admin)
        with patch.object(self.store, "get_user", side_effect=database_app.DatabaseConfigError("secret-token")):
            for client in self.clients:
                self.assertEqual(client.get("/admin/users").status_code, 503)
                response = self.login(client)
                self.assertEqual(response.status_code, 503)
                self.assertNotIn(b"secret-token", response.data)

    def test_empty_admin_allowlist_does_not_grant_automatic_privileges(self):
        for module, client in zip((database_app, tools_app), self.clients):
            self.login(client, self.admin)
            with patch.dict(module.app.config, {"AUTH_ADMIN_EMAILS": ""}):
                self.assertEqual(client.get("/admin/users").status_code, 403)

    def test_debug_auth_bypass_does_not_allow_account_administration(self):
        for module, client in zip((database_app, tools_app), self.clients):
            with patch.object(module, "AUTH_REQUIRED", False):
                self.assertEqual(client.get("/admin/users").status_code, 403)

    def test_malformed_password_hash_cannot_crash_login(self):
        self.store.users[self.employee]["password_hash"] = "malformed:hash"
        for client in self.clients:
            self.assertEqual(self.login(client).status_code, 200)

    def test_sessions_without_password_binding_require_new_login(self):
        for client in self.clients:
            with client.session_transaction() as session:
                session["user_id"] = self.employee
                session["user_email"] = self.admin
            self.assertEqual(client.get("/").status_code, 302)

    def test_team_check_does_not_create_accounts_or_print_secrets(self):
        result = database_app.app.test_cli_runner().invoke(args=["users", "check-team"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("missing or inactive", result.output)
        self.assertEqual(len(self.store.users), 2)

    def test_new_user_does_not_gain_s_database_permissions_or_admin_role(self):
        client = self.clients[0]
        self.login(client, self.admin)
        self.admin_post(client, email="new@wildwildgroup.com", password=self.password, role="admin")
        for other in self.clients:
            self.login(other, "new@wildwildgroup.com")
            self.assertNotIn(b"S Live DB", other.get("/").data)
            self.assertEqual(other.get("/admin/users").status_code, 403)
        self.assertEqual(client.get("/api/databases/s/apps").status_code, 403)

    def test_tools_login_next_cannot_redirect_to_an_external_host(self):
        client = self.clients[1]
        for target in ("//evil.test", "/\\evil.test", "https://evil.test"):
            self.assertEqual(self.login(client, next=target).headers["Location"], "/")
        self.assertEqual(self.login(client, next="/availability").headers["Location"], "/availability")


class SQLiteManualAccountTests(unittest.TestCase):
    def test_legacy_sqlite_migration_preserves_hashes_and_adds_only_access_column(self):
        for module in (database_app, tools_app):
            with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                path = Path(directory) / "users.sqlite"
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT UNIQUE, password_hash TEXT, active INTEGER, created_at TEXT, last_login_at TEXT)")
                    connection.execute("INSERT INTO users VALUES (1, 'old@wildwildgroup.com', 'existing-hash', 1, 'created', 'login')")
                stack.enter_context(patch.object(module, "AUTH_STORAGE", "sqlite"))
                stack.enter_context(patch.object(module, "AUTH_DB_PATH", str(path) if module is tools_app else path))
                user = module.get_user_by_email("old@wildwildgroup.com")
                self.assertEqual(user["password_hash"], "existing-hash")
                self.assertIsNone(user["database_access"])
                self.assertEqual(account_database_access(user), {"wwa"})
                module.update_user(user["email"], database_access="s")
                self.assertEqual(module.get_user_by_email(user["email"])["database_access"], "s")

    def test_deletion_removes_sqlite_record_and_invalidates_sessions_on_each_site(self):
        for module in (database_app, tools_app):
            with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                path = Path(directory) / "users.sqlite"
                stack.enter_context(patch.object(module, "AUTH_STORAGE", "sqlite"))
                stack.enter_context(patch.object(module, "AUTH_REQUIRED", True))
                stack.enter_context(patch.object(module, "AUTH_DB_PATH", str(path) if module is tools_app else path))
                stack.enter_context(patch.dict(module.app.config, {
                    "TESTING": True, "SESSION_COOKIE_SECURE": False, "AUTH_ADMIN_EMAILS": "admin@wildwildgroup.com",
                }))
                admin = module.create_user("admin@wildwildgroup.com", "test-password")
                employee = module.create_user("employee@wildwildgroup.com", "test-password")
                client = module.app.test_client()
                with client.session_transaction() as session:
                    session["user_id"] = admin["id"]
                    session["user_email"] = admin["email"]
                    session["csrf_token"] = "test-csrf"
                    with module.app.app_context():
                        session["account_token"] = module.account_session_token(admin)
                response = client.post(f"/admin/users/{employee['email']}", data={
                    "action": "delete", "confirm_email": employee["email"], "csrf_token": "test-csrf",
                })
                self.assertEqual(response.status_code, 303)
                self.assertIsNone(module.get_user_by_id(employee["id"]))
                self.assertEqual(len(module.list_users()), 1)
                with self.assertRaises(ValueError):
                    module.delete_user(employee["email"])
                module.create_user(employee["email"], "test-password")
                self.assertNotEqual(module.get_user_by_email(employee["email"])["id"], employee["id"])

    def test_manual_accounts_survive_restart_and_can_be_managed_on_both_sites(self):
        for module in (database_app, tools_app):
            with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                stack.enter_context(patch.object(module, "AUTH_STORAGE", "sqlite"))
                stack.enter_context(patch.object(module, "AUTH_REQUIRED", True))
                stack.enter_context(patch.object(module, "AUTH_DB_PATH", str(Path(directory) / "users.sqlite") if module is tools_app else Path(directory) / "users.sqlite"))
                stack.enter_context(patch.dict(module.app.config, {
                    "TESTING": True, "SESSION_COOKIE_SECURE": False, "AUTH_ADMIN_EMAILS": "admin@wildwildgroup.com",
                }))
                runner = module.app.test_cli_runner()
                result = runner.invoke(args=["users", "add", "admin@wildwildgroup.com"],
                                       input="local-test-password\nlocal-test-password\n")
                self.assertEqual(result.exit_code, 0, result.output)
                original = module.get_user_by_email("admin@wildwildgroup.com")
                self.assertEqual(original["database_access"], "wwa")
                changed = module.update_user(original["email"], database_access="s")
                self.assertEqual(changed["database_access"], "s")
                self.assertEqual(changed["password_hash"], original["password_hash"])
                client = module.app.test_client()
                client.get("/login")
                with client.session_transaction() as session:
                    csrf = session["csrf_token"]
                response = client.post("/login", data={"email": original["email"], "password": "local-test-password", "csrf_token": csrf})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(client.get("/admin/users").status_code, 200)
                self.assertEqual(module.get_user_by_email(original["email"])["password_hash"], original["password_hash"])
                module.update_user(original["email"], password="new-local-password")
                self.assertEqual(client.get("/").status_code, 302)
                with self.assertRaises(ValueError):
                    module.create_user(original["email"], "must-not-overwrite")


class GoogleSheetsUserStoreTests(unittest.TestCase):
    def test_legacy_users_schema_migration_preserves_existing_account_rows(self):
        sheets = Mock()
        sheets.get_sheet_titles.return_value = {"Users"}
        sheets.get_values.side_effect = lambda sheet, cells: (
            [database_app.LEGACY_USERS_SHEET_HEADERS] if cells == "A1:F1" else
            [["employee@wildwildgroup.com", "old-hash", "TRUE", "created", "login"]]
        )
        store = database_app.GoogleSheetsUserStore(sheets)
        user = store.get_user("employee@wildwildgroup.com")
        self.assertEqual(user["password_hash"], "old-hash")
        self.assertEqual(account_database_access(user), {"wwa"})
        sheets.update_values.assert_called_once_with("Users", "F1:F1", [["database_access"]])
        store.ensure_ready()
        self.assertEqual(sheets.update_values.call_count, 1)

    def test_access_edit_changes_only_the_access_column(self):
        sheets = Mock()
        stored = [["employee@wildwildgroup.com", "hash", "TRUE", "created", "login", "wwa"]]
        sheets.get_values.side_effect = lambda sheet, cells: [list(row) for row in stored]
        def write(sheet, cells, values):
            self.assertEqual((sheet, cells), ("Users", "F2:F2"))
            stored[0][5] = values[0][0]
        sheets.update_values.side_effect = write
        store = database_app.GoogleSheetsUserStore(sheets)
        store._ready = True
        self.assertEqual(store.update_user("employee@wildwildgroup.com", database_access="s")["database_access"], "s")
        self.assertEqual(stored[0], ["employee@wildwildgroup.com", "hash", "TRUE", "created", "login", "s"])

    def test_batch_clear_uses_exact_quoted_ranges(self):
        sheets = database_app.GoogleSheetsStore(spreadsheet_id="test")
        with patch.object(sheets, "_request") as request:
            sheets.clear_value_ranges("Team's Users", ["A2:E2", "A5:E5"])
        request.assert_called_once_with("POST", "/values:batchClear", json={
            "ranges": ["'Team''s Users'!A2:E2", "'Team''s Users'!A5:E5"],
        })

    def test_delete_erases_all_matching_account_fields_without_shifting_other_rows(self):
        sheets = Mock()
        stored = [["admin@wildwildgroup.com", "admin-hash", "TRUE", "created", "login"], [],
                  ["Employee@wildwildgroup.com", "hash", "TRUE", "created", "login"],
                  ["employee@wildwildgroup.com", "duplicate-hash", "FALSE", "created", "login"],
                  ["other@wildwildgroup.com", "other-hash", "TRUE", "created", "login"]]
        sheets.get_values.side_effect = lambda sheet, cells: [list(row) for row in stored]

        def clear(sheet, ranges):
            self.assertEqual(sheet, "Users")
            self.assertEqual(ranges, ["A4:F4", "A5:F5"])
            stored[2] = []
            stored[3] = []

        sheets.clear_value_ranges.side_effect = clear
        store = database_app.GoogleSheetsUserStore(sheets)
        store._ready = True
        store.delete_user(" EMPLOYEE@wildwildgroup.com ")
        self.assertIsNone(store.get_user("employee@wildwildgroup.com"))
        self.assertEqual(len(store.load_users()), 2)
        self.assertEqual(stored[0][1], "admin-hash")
        self.assertEqual(stored[4][1], "other-hash")
        sheets.clear_value_ranges.assert_called_once()
        sheets.update_values.assert_not_called()
        sheets.append_values.assert_not_called()

    def test_delete_missing_or_empty_email_never_clears_a_range(self):
        sheets = Mock()
        sheets.get_values.return_value = [[], ["other@wildwildgroup.com"]]
        store = database_app.GoogleSheetsUserStore(sheets)
        store._ready = True
        for email in ("", "missing@wildwildgroup.com"):
            with self.assertRaises(ValueError):
                store.delete_user(email)
        sheets.clear_value_ranges.assert_not_called()

    def test_google_credential_failure_is_a_storage_error(self):
        sheets = database_app.GoogleSheetsStore(spreadsheet_id="test", service_account_json="{}")
        with self.assertRaises(database_app.DatabaseConfigError):
            sheets._token()

    def test_explicit_auth_sheet_settings_override_the_apps_database(self):
        with patch.object(database_app, "AUTH_USER_STORE", None), patch.dict("os.environ", {
            "AUTH_SPREADSHEET_ID": "shared-users-only",
            "AUTH_SERVICE_ACCOUNT_JSON": "configured-secret",
        }):
            store = database_app.build_user_store()
            self.assertEqual(store.sheets_store.spreadsheet_id, "shared-users-only")
            self.assertEqual(store.sheets_store.service_account_json, "configured-secret")

    def test_account_edit_changes_only_credentials_and_status(self):
        sheets = Mock()
        stored = [["employee@wildwildgroup.com", "existing-hash", "TRUE", "created", "last-login"]]
        sheets.get_sheet_titles.return_value = {"Users"}
        sheets.get_values.side_effect = lambda sheet, cells: (
            [database_app.USERS_SHEET_HEADERS] if cells == "A1:F1" else [list(row) for row in stored]
        )

        def write(_sheet, cell_range, rows):
            self.assertIn(cell_range, {"B2:B2", "C2:C2"})
            stored[0][1 if cell_range == "B2:B2" else 2] = rows[0][0]

        sheets.update_values.side_effect = write
        store = database_app.GoogleSheetsUserStore(sheets)
        changed = store.update_user("EMPLOYEE@wildwildgroup.com", password_hash="new-hash")
        self.assertEqual(changed["password_hash"], "new-hash")
        self.assertEqual(changed["active"], 1)
        changed = store.update_user("employee@wildwildgroup.com", active=0)
        self.assertEqual(changed["active"], 0)
        self.assertEqual(stored[0], ["employee@wildwildgroup.com", "new-hash", "FALSE", "created", "last-login"])
        sheets.append_values.assert_not_called()
        sheets.add_sheet.assert_not_called()

    def test_existing_account_is_never_overwritten_by_create(self):
        sheets = Mock()
        sheets.get_values.return_value = [["Existing@wildwildgroup.com", "old-hash", "TRUE"]]
        store = database_app.GoogleSheetsUserStore(sheets)
        store._ready = True
        with self.assertRaises(ValueError):
            store.create_user("existing@wildwildgroup.com", "new-hash")
        sheets.append_values.assert_not_called()
        sheets.update_values.assert_not_called()


if __name__ == "__main__":
    unittest.main()
