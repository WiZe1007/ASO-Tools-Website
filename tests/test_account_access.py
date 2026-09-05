import tempfile
import sqlite3
import re
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import Mock, patch

from werkzeug.security import check_password_hash, generate_password_hash

import app as tools_app
import database_site.app as database_app
from account_access import LoginAttemptLimiter, TEAM_EMAILS, account_database_access, verify_account_password
from tests.test_database_site import FakeStore, FakeUserStore


class SeparateAccountTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.store = FakeUserStore()
        self.tools_store = FakeUserStore()
        self.stores = (self.store, self.tools_store)
        self.stack.enter_context(patch.object(database_app, "AUTH_STORAGE", "google_sheets"))
        self.stack.enter_context(patch.object(database_app, "AUTH_USER_STORE", self.store))
        self.stack.enter_context(patch.object(database_app, "S_ALLOWED_EMAILS", ""))
        self.stack.enter_context(patch.object(tools_app, "AUTH_STORAGE", "google_sheets"))
        self.stack.enter_context(patch.object(tools_app, "TOOLS_AUTH_USER_STORE", self.tools_store))
        self.stack.enter_context(patch.object(tools_app, "TOOLS_AUTH_USER_CACHE", tools_app.TTLCache(60)))
        self.stack.enter_context(patch.object(tools_app, "S_LIVE_DB_ALLOWED_EMAILS", ""))
        for module in (database_app, tools_app):
            self.stack.enter_context(patch.object(module, "AUTH_REQUIRED", True))
            self.stack.enter_context(patch.object(module, "AUTH_ALLOWED_EMAIL_DOMAIN", "@wildwildgroup.com"))
            self.stack.enter_context(patch.object(
                module, "LOGIN_ATTEMPT_LIMITER", LoginAttemptLimiter(max_failures=6, window_seconds=900)
            ))
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
        for store in self.stores:
            for email in (self.admin, self.employee):
                store.create_user(email, password_hash)
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

    def logout(self, client, **kwargs):
        return client.post("/logout", data={"csrf_token": self.csrf(client)}, **kwargs)

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
        for store in self.stores:
            for email in TEAM_EMAILS:
                store.create_user(email, password_hash)
        for client, store in zip(self.clients, self.stores):
            for email in TEAM_EMAILS:
                with self.subTest(email=email):
                    self.assertEqual(self.login(client, email, "Existing-team-password").status_code, 302)
                    self.assertEqual(client.get("/").status_code, 200)
                    self.assertEqual(store.users[email]["password_hash"], password_hash)
                    self.logout(client)
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

    def test_admin_adds_user_only_to_current_site_without_changing_own_session(self):
        for index, client in enumerate(self.clients):
            self.login(client, self.admin)
            email = f"new{index}@wildwildgroup.com"
            result = self.admin_post(client, email=email.upper(), password=self.password)
            self.assertEqual(result.status_code, 303)
            self.assertEqual(result.headers["Location"], "/admin/users")
            with client.session_transaction() as session:
                self.assertEqual(session["user_email"], self.admin)
            self.assertTrue(check_password_hash(self.stores[index].users[email]["password_hash"], self.password))
            self.assertNotIn(email, self.stores[1 - index].users)
            for other_index, module in enumerate((database_app, tools_app)):
                self.assertEqual(self.login(module.app.test_client(), email).status_code, 302 if other_index == index else 200)

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
        for index, client in enumerate(self.clients[:1]):
            self.login(client, self.admin)
            for scope in ([], ["wwa"], ["s"], ["wwa", "s"]):
                email = f"new{index}.{len(scope)}.{scope[0] if scope else 'none'}@wildwildgroup.com"
                response = self.admin_post(client, email=email, password=self.password, database_access=scope)
                self.assertEqual(response.status_code, 303)
                self.assertEqual(account_database_access(self.store.users[email], email), set(scope))

    def test_database_access_edits_do_not_change_tools_access(self):
        for admin_client in self.clients[:1]:
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
                            for method, suffix in ((db_client.post, ""), (db_client.patch, "/2"), (db_client.delete, "/2")):
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
                        self.assertEqual(tools_client.get(f"/{path}").status_code, 200 if key == "wwa" else 403)
                        self.assertEqual(tools_client.get(f"/api/{path}").status_code, 200 if key == "wwa" else 403)
                        if key == "s":
                            payload.assert_not_called()
                    nav = tools_client.get("/").data
                    self.assertIn(b'href="/live-apps"', nav)
                    self.assertNotIn(b'href="/s-live-apps"', nav)

    def test_access_revocation_on_another_worker_bypasses_cached_permissions(self):
        self.store.update_user(self.employee, database_access="s,wwa")
        for client in self.clients:
            self.login(client)
        self.assertEqual(database_app.AUTH_USER_CACHE[self.employee][1]["database_access"], "s,wwa")
        self.store.update_user(self.employee, database_access="none")
        self.assertEqual(self.clients[0].get("/api/apps").status_code, 403)
        self.assertEqual(self.clients[0].get("/api/databases/s/apps").status_code, 403)
        database_app.cache_user(self.employee, {**self.store.get_user(self.employee), "database_access": "s,wwa"})
        with patch.object(database_app, "build_store") as build:
            response = self.clients[0].delete("/api/apps/2", json={"expected_app_id": "com.test.app"},
                                              headers={"X-CSRF-Token": self.csrf(self.clients[0])})
            self.assertEqual(response.status_code, 403)
            build.assert_not_called()
        with patch.object(tools_app, "build_live_apps_database_payload", return_value={}):
            self.assertEqual(self.clients[1].get("/api/live-apps").status_code, 200)
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
            response = client.delete("/api/databases/s/apps/2", json={"expected_app_id": "com.test.app"}, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(FakeStore.s_apps, [])
            self.assertEqual(FakeStore.apps, [])

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
        for client in self.clients[:1]:
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
        for client, store in zip(self.clients, self.stores):
            original = store.users[self.employee]["password_hash"]
            self.login(client, self.admin)
            result = self.admin_post(client, email=self.employee.upper(), password="different-password")
            self.assertEqual(result.status_code, 409)
            self.assertEqual(store.users[self.employee]["password_hash"], original)

    def test_delete_revokes_only_current_site_and_recreation_does_not_restore_session(self):
        for index, module in enumerate((database_app, tools_app)):
            for client in self.clients:
                self.assertEqual(self.login(client).status_code, 302)
            admin_client = module.app.test_client()
            self.login(admin_client, self.admin)
            response = self.admin_post(admin_client, f"/admin/users/{self.employee}",
                                       action="delete", confirm_email=self.employee)
            self.assertEqual(response.status_code, 303)
            self.assertNotIn(self.employee, self.stores[index].users)
            self.assertIn(self.employee, self.stores[1 - index].users)
            self.assertNotIn(self.employee.encode(), admin_client.get("/admin/users").data)
            self.assertIn(self.admin, self.stores[index].users)
            self.assertEqual(admin_client.get("/admin/users").status_code, 200)
            self.assertIsNone(module.get_user_by_email(self.employee))
            self.assertEqual(self.login(module.app.test_client()).status_code, 200)
            self.assertEqual(self.clients[1 - index].get("/").status_code, 200)
            # Recreating the same email/password must not revive its old sessions.
            options = {"database_access": ["wwa"]} if module is database_app else {}
            self.admin_post(admin_client, email=self.employee, password=self.password, **options)
            self.assertEqual(self.clients[index].get("/").status_code, 302)
            self.assertEqual(self.clients[1 - index].get("/").status_code, 200)

    def test_disabled_account_can_be_permanently_deleted(self):
        for index, client in enumerate(self.clients):
            email = f"disabled{index}@wildwildgroup.com"
            store = self.stores[index]
            store.create_user(email, generate_password_hash(self.password))
            store.update_user(email, active=0)
            self.login(client, self.admin)
            response = self.admin_post(client, f"/admin/users/{email}", action="delete", confirm_email=email)
            self.assertEqual(response.status_code, 303)
            self.assertNotIn(email, store.users)

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
        for module, client, store in zip((database_app, tools_app), self.clients, self.stores):
            self.login(client, self.admin)
            module.get_user_by_email(self.employee)
            with patch.object(store, "delete_user", side_effect=database_app.DatabaseConfigError("secret")):
                response = self.admin_post(client, f"/admin/users/{self.employee}", action="delete", confirm_email=self.employee)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(b"secret", response.data)
            if module is database_app:
                self.assertNotIn(self.employee, database_app.AUTH_USER_CACHE)
            else:
                self.assertIs(tools_app.TOOLS_AUTH_USER_CACHE.get((self.employee,)), tools_app.CACHE_MISS)
            self.assertIn(self.employee, store.users)

    def test_deleted_admin_cannot_delete_others_even_with_cached_session(self):
        for client in self.clients:
            self.login(client, self.admin)
        for client, store in zip(self.clients, self.stores):
            del store.users[self.admin]
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

    def test_password_reset_and_disable_affect_only_current_site(self):
        for index, module in enumerate((database_app, tools_app)):
            for client, store in zip(self.clients, self.stores):
                store.update_user(self.employee, password_hash=generate_password_hash(self.password), active=1)
                self.login(client)
            admin_client = module.app.test_client()
            self.login(admin_client, self.admin)
            result = self.admin_post(admin_client, f"/admin/users/{self.employee}",
                                     action="password", password="new-valid-password")
            self.assertEqual(result.status_code, 303)
            self.assertEqual(self.clients[index].get("/").status_code, 302)
            self.assertEqual(self.login(self.clients[index]).status_code, 200)
            self.assertEqual(self.login(self.clients[index], password="new-valid-password").status_code, 302)
            self.assertEqual(self.clients[1 - index].get("/").status_code, 200)
            self.assertEqual(self.login(self.clients[1 - index]).status_code, 302)
            self.assertEqual(self.admin_post(admin_client, f"/admin/users/{self.employee}", action="status", active="0").status_code, 303)
            self.assertEqual(self.clients[index].get("/").status_code, 302)
            self.assertEqual(self.clients[1 - index].get("/").status_code, 200)

    def test_disabled_user_cannot_log_in_even_with_stale_cached_active_user(self):
        for client in self.clients:
            self.login(client)
        for client, store in zip(self.clients, self.stores):
            store.users[self.employee]["active"] = 0
            self.assertEqual(self.login(client).status_code, 200)
            self.assertEqual(client.get("/").status_code, 302)

    def test_admin_can_disable_and_reactivate_but_not_disable_self(self):
        for client, store in zip(self.clients, self.stores):
            self.login(client, self.admin)
            for active in ("0", "1"):
                result = self.admin_post(client, f"/admin/users/{self.employee}", action="status", active=active)
                self.assertEqual(result.status_code, 303)
                self.assertEqual(store.users[self.employee]["active"], int(active))
            result = self.admin_post(client, f"/admin/users/{self.admin}", action="status", active="0")
            self.assertEqual(result.status_code, 400)

    def test_admin_recheck_ignores_cached_privileges(self):
        for client in self.clients:
            self.login(client, self.admin)
        for client, store in zip(self.clients, self.stores):
            store.users[self.admin]["active"] = 0
            self.assertIn(client.get("/admin/users").status_code, (302, 403))

    def test_storage_outage_fails_closed_without_local_password_fallback(self):
        for client in self.clients:
            self.login(client, self.admin)
        for client, store in zip(self.clients, self.stores):
            with patch.object(store, "get_user", side_effect=database_app.DatabaseConfigError("secret-token")):
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
        for client, store in zip(self.clients, self.stores):
            store.users[self.employee]["password_hash"] = "malformed:hash"
            self.assertEqual(self.login(client).status_code, 200)

    def test_missing_and_inactive_accounts_still_run_password_hash_check(self):
        with patch("account_access.check_password_hash", return_value=False) as password_check:
            self.assertFalse(verify_account_password(None, self.password))
            self.assertFalse(verify_account_password({
                "active": "invalid",
                "password_hash": generate_password_hash(self.password),
            }, self.password))
        self.assertEqual(password_check.call_count, 2)

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
        for client in self.clients:
            self.login(client, self.admin)
            self.admin_post(client, email="new@wildwildgroup.com", password=self.password, role="admin")
            self.login(client, "new@wildwildgroup.com")
            self.assertNotIn(b"S Live DB", client.get("/").data)
            self.assertEqual(client.get("/admin/users").status_code, 403)
        self.assertEqual(self.clients[0].get("/api/databases/s/apps").status_code, 403)

    def test_tools_login_next_cannot_redirect_to_an_external_host(self):
        client = self.clients[1]
        for target in ("//evil.test", "/\\evil.test", "https://evil.test"):
            self.assertEqual(self.login(client, next=target).headers["Location"], "/")
        self.assertEqual(self.login(client, next="/availability").headers["Location"], "/availability")

    def test_tools_admin_never_shows_or_accepts_database_permissions(self):
        client = self.clients[1]
        self.login(client, self.admin)
        for group in ("", "wwa", "s"):
            response = client.get(f"/admin/users?group={group}")
            self.assertEqual(response.status_code, 200)
            self.assertIn(self.employee.encode(), response.data)
            for hidden in (b'WWA DB', b'S DB', b'name="database_access"', b'value="access"'):
                self.assertNotIn(hidden, response.data)
        self.assertEqual(self.admin_post(client, f"/admin/users/{self.employee}", action="access", database_access=["s"]).status_code, 400)
        self.assertEqual(self.admin_post(client, email="blocked@wildwildgroup.com", password=self.password, database_access=["wwa"]).status_code, 400)
        self.assertNotIn("blocked@wildwildgroup.com", self.tools_store.users)

    def test_tools_s_live_db_uses_only_its_own_allowlist(self):
        self.store.update_user(self.employee, database_access="none")
        client = self.clients[1]
        self.login(client)
        with patch.object(tools_app, "S_LIVE_DB_ALLOWED_EMAILS", self.employee), \
             patch.object(tools_app, "build_live_apps_database_payload", return_value={}), \
             patch.object(tools_app, "build_s_live_apps_store", return_value=Mock()):
            self.assertEqual(client.get("/api/s-live-apps").status_code, 200)
            self.assertIn(b'href="/s-live-apps"', client.get("/").data)
        self.assertEqual(client.get("/api/s-live-apps").status_code, 403)

    def test_user_cache_is_separate_even_for_the_same_email(self):
        self.tools_store.users[self.employee]["password_hash"] = "tools-only-hash"
        self.assertEqual(tools_app.get_user_by_email(self.employee)["password_hash"], "tools-only-hash")
        self.assertNotEqual(database_app.get_user_by_email(self.employee)["password_hash"], "tools-only-hash")
        database_app.cache_user(self.employee, None)
        self.assertIsNone(database_app.get_user_by_email(self.employee))
        self.assertEqual(tools_app.get_user_by_email(self.employee)["password_hash"], "tools-only-hash")

    def test_admin_allowlists_are_independent(self):
        for client in self.clients:
            self.login(client, self.admin)
        with patch.dict(tools_app.app.config, {"AUTH_ADMIN_EMAILS": self.employee}):
            self.assertEqual(self.clients[0].get("/admin/users").status_code, 200)
            self.assertEqual(self.clients[1].get("/admin/users").status_code, 403)

    def test_sessions_cannot_be_replayed_between_sites_even_with_same_secret(self):
        modules = (database_app, tools_app)
        self.assertNotEqual(modules[0].app.config["SESSION_COOKIE_NAME"], modules[1].app.config["SESSION_COOKIE_NAME"])
        with patch.dict(database_app.app.config, {"SECRET_KEY": "shared-test-secret"}), \
             patch.dict(tools_app.app.config, {"SECRET_KEY": "shared-test-secret"}):
            for index, module in enumerate(modules):
                source = module.app.test_client()
                self.login(source)
                with source.session_transaction() as session:
                    payload = dict(session)
                target = modules[1 - index].app.test_client()
                with target.session_transaction() as session:
                    session.update(payload)
                self.assertEqual(target.get("/").status_code, 302)

    def test_repeated_failed_logins_are_throttled_on_both_sites(self):
        for module, client in zip((database_app, tools_app), self.clients):
            for attempt in range(6):
                response = self.login(client, password="definitely-wrong-password")
                self.assertEqual(response.status_code, 429 if attempt == 5 else 200)
            self.assertGreater(int(response.headers["Retry-After"]), 0)
            self.assertEqual(self.login(client).status_code, 429)
            module.LOGIN_ATTEMPT_LIMITER.clear()
            self.assertEqual(self.login(client).status_code, 302)

    def test_expensive_tools_share_one_per_user_rate_limit(self):
        client = self.clients[1]
        self.assertEqual(self.login(client).status_code, 302)
        limiter = LoginAttemptLimiter(max_failures=2, window_seconds=60)
        with patch.object(tools_app, "HEAVY_REQUEST_LIMITER", limiter):
            first = client.post("/check", json={"url": "invalid"})
            second = client.post("/availability/check", json={"url": "invalid"})
            blocked = client.post("/api/indexing/check", json={"app_id": "invalid"})
        self.assertEqual(first.status_code, 400)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.get_json()["error"], "RATE_LIMITED")
        self.assertGreater(int(blocked.headers["Retry-After"]), 0)

    def test_cross_origin_browser_writes_are_blocked_on_both_sites(self):
        hostile_headers = {
            "Origin": "https://attacker.onrender.com",
            "Sec-Fetch-Site": "same-site",
        }
        for client in self.clients:
            client.get("/login")
            response = client.post("/login", data={
                "email": self.employee,
                "password": self.password,
                "csrf_token": self.csrf(client),
            }, headers=hostile_headers)
            self.assertEqual(response.status_code, 403)

        self.login(self.clients[0])
        response = self.clients[0].post(
            "/api/apps",
            json={"app_input": "com.cross.site"},
            headers={"X-CSRF-Token": self.csrf(self.clients[0]), **hostile_headers},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "CROSS_ORIGIN_REQUEST_BLOCKED")

        self.login(self.clients[1])
        response = self.clients[1].post("/check", json={}, headers=hostile_headers)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "CROSS_ORIGIN_REQUEST_BLOCKED")

    def test_hostile_referer_is_blocked_when_origin_is_missing(self):
        for client in self.clients:
            client.get("/login")
            response = client.post("/login", data={
                "email": self.employee,
                "password": self.password,
                "csrf_token": self.csrf(client),
            }, headers={"Referer": "https://attacker.example/login"})
            self.assertEqual(response.status_code, 403)

    def test_null_origin_requires_same_origin_fetch_metadata(self):
        for client in self.clients:
            client.get("/login")
            data = {
                "email": self.employee,
                "password": self.password,
                "csrf_token": self.csrf(client),
            }
            rejected = client.post("/login", data=data, headers={"Origin": "null"})
            self.assertEqual(rejected.status_code, 403)
            accepted = client.post("/login", data=data, headers={
                "Origin": "null",
                "Sec-Fetch-Site": "same-origin",
            })
            self.assertEqual(accepted.status_code, 302)

    def test_cross_site_logout_cannot_clear_an_authenticated_session(self):
        for client in self.clients:
            self.assertEqual(self.login(client).status_code, 302)
            self.assertEqual(client.get("/logout").status_code, 405)
            self.assertEqual(client.post("/logout").status_code, 403)
            response = self.logout(client, headers={
                "Referer": "https://attacker.onrender.com/",
                "Sec-Fetch-Site": "same-site",
            })
            self.assertEqual(response.status_code, 403)
            self.assertEqual(client.get("/").status_code, 200)

    def test_login_request_body_is_limited_on_both_sites(self):
        for client in self.clients:
            client.get("/login")
            response = client.post("/login", data={
                "email": self.employee,
                "password": "x" * (70 * 1024),
                "csrf_token": self.csrf(client),
            })
            self.assertEqual(response.status_code, 413)

    def test_security_headers_and_secure_cookie_are_set_on_both_sites(self):
        expected_headers = {
            "Content-Security-Policy",
            "X-Frame-Options",
            "X-Content-Type-Options",
            "Referrer-Policy",
            "Permissions-Policy",
            "Cross-Origin-Opener-Policy",
        }
        for module in (database_app, tools_app):
            with patch.dict(module.app.config, {"SESSION_COOKIE_SECURE": True}):
                response = module.app.test_client().get("/login")
            self.assertTrue(expected_headers <= set(response.headers.keys()))
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            csp = response.headers["Content-Security-Policy"]
            self.assertIn("frame-ancestors 'none'", csp)
            self.assertRegex(csp, r"script-src 'self' 'nonce-[A-Za-z0-9_-]+'")
            self.assertNotIn("script-src 'self' 'unsafe-inline'", csp)
            self.assertIn("no-store", response.headers["Cache-Control"])
            self.assertIn("max-age=31536000", response.headers["Strict-Transport-Security"])
            cookie = response.headers.get("Set-Cookie", "")
            self.assertIn("Secure", cookie)
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Lax", cookie)

    def test_inline_scripts_use_the_response_csp_nonce(self):
        for client in self.clients:
            self.assertEqual(self.login(client).status_code, 302)
            response = client.get("/")
            csp = response.headers["Content-Security-Policy"]
            nonce = re.search(r"script-src 'self' 'nonce-([^']+)'", csp).group(1)
            self.assertIn(f'<script nonce="{nonce}">'.encode(), response.data)

    def test_csv_export_neutralizes_spreadsheet_formulas(self):
        source = (Path(__file__).resolve().parents[1] / "static" / "js" / "site-enhance.js").read_text()
        self.assertIn("/^[=+\\-@]/.test(value)", source)
        self.assertIn("value = `'${value}`", source)

    def test_render_runtime_cannot_use_local_only_tools_endpoints(self):
        client = self.clients[1]
        self.login(client)
        with patch.dict(tools_app.os.environ, {"RENDER": "true"}), \
             patch.object(tools_app, "_clear_appmagic_cached_auth") as clear_auth, \
             patch.object(tools_app, "_save_appmagic_cached_auth") as save_auth:
            status = client.get("/appmagic/auth/status").get_json()
            self.assertFalse(status["is_local"])
            self.assertFalse(status["can_auto_import"])
            self.assertEqual(client.post("/appmagic/auth/exchange", json={"url": "secret"}).status_code, 400)
            self.assertEqual(client.post("/appmagic/auth/auto-import", json={}).status_code, 400)
            self.assertEqual(client.post("/shutdown").status_code, 403)
            self.assertEqual(client.post("/exit").status_code, 403)
            token_response = client.post("/appmagic/auth/token", json={"token": "A" * 24})
            self.assertEqual(token_response.status_code, 200)
            self.assertEqual(token_response.get_json()["auth"]["source"], "browser_session")
            self.assertEqual(client.post("/appmagic/auth/logout").status_code, 200)
            clear_auth.assert_not_called()
            save_auth.assert_not_called()
            with patch.object(tools_app, "AVAILABILITY_TASK_SECRET", ""), \
                 tools_app.app.test_request_context("/tasks/check-availability", method="POST"):
                self.assertFalse(tools_app.task_request_authorized())

    def test_task_secret_is_never_accepted_from_the_url(self):
        with patch.object(tools_app, "AVAILABILITY_TASK_SECRET", "strong-task-secret"):
            with tools_app.app.test_request_context(
                "/tasks/check-availability?secret=strong-task-secret", method="POST"
            ):
                self.assertFalse(tools_app.task_request_authorized())
            with tools_app.app.test_request_context(
                "/tasks/check-availability", method="POST",
                headers={"X-Task-Secret": "strong-task-secret"},
            ):
                self.assertTrue(tools_app.task_request_authorized())
            self.assertEqual(self.clients[1].get("/tasks/check-availability").status_code, 405)

    def test_sheet_urls_are_rebuilt_as_google_play_links(self):
        malicious = {
            "app_id": "com.example.safe",
            "app_url": "javascript:alert(document.cookie)",
            "app_name": "Example",
        }
        database_payload = database_app.app_payload(malicious)
        tools_payload = tools_app.availability_app_payload(malicious)
        for payload in (database_payload, tools_payload):
            self.assertTrue(payload["app_url"].startswith("https://play.google.com/store/apps/details?"))
            self.assertNotIn("javascript:", payload["app_url"])
        invalid = {"app_id": "javascript:alert(1)", "app_url": "javascript:alert(1)"}
        self.assertEqual(database_app.app_payload(invalid)["app_url"], "")
        self.assertEqual(tools_app.availability_app_payload(invalid)["app_url"], "")

    def test_deceptive_store_domains_are_rejected(self):
        deceptive_urls = (
            "https://play.google.com.attacker.test/store/apps/details?id=com.example.app",
            "https://user:password@play.google.com/store/apps/details?id=com.example.app",
            "https://play.google.com:bad/store/apps/details?id=com.example.app",
            "javascript:play.google.com/store/apps/details?id=com.example.app",
            "https://[bad",
            "\x00https://play.google.com/store/apps/details?id=com.example.app",
        )
        for deceptive in deceptive_urls:
            with self.subTest(url=deceptive):
                self.assertEqual(tools_app.detect_store(deceptive), "unknown")
                self.assertEqual(tools_app.extract_google_play_app_id(deceptive), "")
                self.assertEqual(tools_app.normalize_android_package_input(deceptive), "")
                self.assertEqual(database_app.normalize_package_input(deceptive), "")
        valid = "https://play.google.com/store/apps/details?id=com.example.app"
        self.assertEqual(tools_app.detect_store(valid), "google_play")
        self.assertEqual(tools_app.extract_google_play_app_id(valid), "com.example.app")
        self.assertEqual(database_app.normalize_package_input(valid), "com.example.app")


class SQLiteManualAccountTests(unittest.TestCase):
    def test_failed_legacy_copy_rolls_back_tools_table(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.sqlite"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("CREATE TABLE users (email TEXT)")
                connection.execute("INSERT INTO users VALUES ('existing@wildwildgroup.com')")
            with patch.object(tools_app, "AUTH_DB_PATH", str(path)):
                with self.assertRaises(sqlite3.OperationalError):
                    tools_app.ensure_auth_db()
            with closing(sqlite3.connect(path)) as connection:
                self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'tools_users'").fetchone())
                self.assertEqual(connection.execute("SELECT email FROM users").fetchone()[0], "existing@wildwildgroup.com")

    def test_legacy_sqlite_migration_preserves_hashes_in_each_site_schema(self):
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
                if module is database_app:
                    self.assertIsNone(user["database_access"])
                    self.assertEqual(account_database_access(user), {"wwa"})
                    module.update_user(user["email"], database_access="s")
                    self.assertEqual(module.get_user_by_email(user["email"])["database_access"], "s")
                else:
                    self.assertNotIn("database_access", user)
                    with closing(sqlite3.connect(path)) as connection:
                        self.assertEqual(connection.execute("SELECT password_hash FROM users").fetchone()[0], "existing-hash")

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
                if module is database_app:
                    self.assertEqual(original["database_access"], "wwa")
                    changed = module.update_user(original["email"], database_access="s")
                    self.assertEqual(changed["database_access"], "s")
                    self.assertEqual(changed["password_hash"], original["password_hash"])
                else:
                    self.assertNotIn("database_access", original)
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

    def test_same_sqlite_file_has_independent_accounts_and_one_time_migration(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            path = Path(directory) / "users.sqlite"
            for module in (database_app, tools_app):
                stack.enter_context(patch.object(module, "AUTH_STORAGE", "sqlite"))
                stack.enter_context(patch.object(module, "AUTH_DB_PATH", str(path) if module is tools_app else path))
            email = "employee@wildwildgroup.com"
            original = database_app.create_user(email, "original-password")
            database_app.update_user(email, active=0, database_access="s")
            copied = tools_app.get_user_by_email(email)
            self.assertEqual(copied["password_hash"], original["password_hash"])
            self.assertEqual(copied["active"], 0)
            self.assertNotIn("database_access", copied)
            tools_app.update_user(email, password="tools-password", active=1)
            self.assertEqual(database_app.get_user_by_email(email)["active"], 0)
            self.assertEqual(database_app.get_user_by_email(email)["password_hash"], original["password_hash"])
            database_app.update_user(email, password="database-password")
            self.assertTrue(check_password_hash(tools_app.get_user_by_email(email)["password_hash"], "tools-password"))
            database_app.create_user("database-only@wildwildgroup.com", "test-password")
            self.assertIsNone(tools_app.get_user_by_email("database-only@wildwildgroup.com"))
            tools_app.delete_user(email)
            tools_app.ensure_auth_db()
            self.assertEqual(tools_app.list_users(), [])
            self.assertIsNotNone(database_app.get_user_by_email(email))
            tools_app.create_user("tools-only@wildwildgroup.com", "test-password")
            self.assertIsNone(database_app.get_user_by_email("tools-only@wildwildgroup.com"))


class GoogleSheetsUserStoreTests(unittest.TestCase):
    def migration_sheets(self, *, existing_target=False):
        sheets = Mock()
        sheets.tabs = {"Users": [database_app.USERS_SHEET_HEADERS[:],
                                ["active@wildwildgroup.com", "hash-active", "TRUE", "created", "login", "s"],
                                [], ["disabled@wildwildgroup.com", "hash-disabled", "FALSE", "created", "", "wwa"]]}
        if existing_target:
            sheets.tabs["ToolsUsers"] = [database_app.LEGACY_USERS_SHEET_HEADERS[:]]
        sheets.get_sheet_titles.side_effect = lambda: set(sheets.tabs)

        def read(sheet, cells):
            rows = sheets.tabs[sheet]
            if cells == "A1:E":
                return [row[:5] for row in rows]
            if cells in ("A1:E1", "A1:F1"):
                return [rows[0][:5 if cells == "A1:E1" else 6]] if rows else []
            if cells in ("A2:E", "A2:F", "A2:A"):
                limit = {"A2:E": 5, "A2:F": 6, "A2:A": 1}[cells]
                return [row[:limit] for row in rows[1:]]
            raise AssertionError(cells)

        def atomic_create(method, path, *, json):
            self.assertEqual((method, path), ("POST", ":batchUpdate"))
            self.assertEqual(len(json["requests"]), 2)
            add, update = json["requests"]
            properties = add["addSheet"]["properties"]
            cells = update["updateCells"]
            self.assertEqual(cells["start"], {"sheetId": properties["sheetId"], "rowIndex": 0, "columnIndex": 0})
            self.assertEqual(cells["fields"], "userEnteredValue")
            self.assertNotIn(properties["title"], sheets.tabs)
            sheets.tabs[properties["title"]] = [
                [value["userEnteredValue"]["stringValue"] for value in row["values"]]
                for row in cells["rows"]
            ]

        sheets.get_values.side_effect = read
        sheets._request.side_effect = atomic_create
        return sheets

    def test_tools_migration_is_atomic_preserves_passwords_and_does_not_copy_permissions(self):
        sheets = self.migration_sheets()
        original = [row[:] for row in sheets.tabs["Users"]]
        store = database_app.GoogleSheetsUserStore(sheets, "ToolsUsers", database_permissions=False)
        store.initialize_from_legacy_sheet("Users")
        users = store.load_users()
        self.assertEqual(len(users), 2)
        self.assertEqual([user["password_hash"] for user in users], ["hash-active", "hash-disabled"])
        self.assertEqual([user["active"] for user in users], [1, 0])
        self.assertTrue(all("database_access" not in user for user in users))
        self.assertEqual(sheets.tabs["Users"], original)
        sheets._request.assert_called_once()
        sheets.add_sheet.assert_not_called()
        sheets.update_values.assert_not_called()

    def test_existing_empty_tools_sheet_is_never_reseeded_after_deletion_or_restart(self):
        sheets = self.migration_sheets(existing_target=True)
        for _ in range(3):
            store = database_app.GoogleSheetsUserStore(sheets, "ToolsUsers", database_permissions=False)
            store.initialize_from_legacy_sheet("Users")
            self.assertEqual(store.load_users(), [])
        sheets._request.assert_not_called()
        self.assertTrue(all(call.args[0] == "ToolsUsers" for call in sheets.get_values.call_args_list))

    def test_migration_never_overwrites_an_existing_tools_password_or_adds_new_database_accounts(self):
        sheets = self.migration_sheets()
        store = database_app.GoogleSheetsUserStore(sheets, "ToolsUsers", database_permissions=False)
        store.initialize_from_legacy_sheet("Users")
        sheets.tabs["ToolsUsers"][1][1] = "new-tools-password-hash"
        sheets.tabs["Users"].append(["new-db-only@wildwildgroup.com", "hash", "TRUE"])
        restarted = database_app.GoogleSheetsUserStore(sheets, "ToolsUsers", database_permissions=False)
        restarted.initialize_from_legacy_sheet("Users")
        self.assertEqual(restarted.get_user("active@wildwildgroup.com")["password_hash"], "new-tools-password-hash")
        self.assertIsNone(restarted.get_user("new-db-only@wildwildgroup.com"))
        self.assertEqual(sheets._request.call_count, 1)

    def test_first_start_without_legacy_sheet_initializes_empty_tools_accounts(self):
        sheets = self.migration_sheets()
        sheets.tabs.clear()
        store = database_app.GoogleSheetsUserStore(sheets, "ToolsUsers", database_permissions=False)
        store.initialize_from_legacy_sheet("Users")
        self.assertEqual(store.load_users(), [])
        self.assertEqual(set(sheets.tabs), {"ToolsUsers"})

    def test_migration_failure_does_not_publish_empty_store_or_fallback_to_database(self):
        sheets = self.migration_sheets()
        sheets._request.side_effect = database_app.DatabaseConfigError("unavailable")
        with patch.object(tools_app, "TOOLS_AUTH_USER_STORE", None), \
             patch.object(tools_app, "TOOLS_AUTH_USERS_SHEET", "ToolsUsers"), \
             patch.object(database_app, "GoogleSheetsStore", return_value=sheets):
            for _ in range(2):
                with self.assertRaises(database_app.DatabaseConfigError):
                    tools_app.build_tools_user_store()
                self.assertIsNone(tools_app.TOOLS_AUTH_USER_STORE)
                self.assertNotIn("ToolsUsers", sheets.tabs)

    def test_uncertain_or_concurrent_create_accepts_only_existing_valid_target(self):
        sheets = self.migration_sheets()
        create = sheets._request.side_effect

        def create_then_timeout(*args, **kwargs):
            create(*args, **kwargs)
            raise database_app.DatabaseConfigError("response lost or another worker created sheet")

        sheets._request.side_effect = create_then_timeout
        store = database_app.GoogleSheetsUserStore(sheets, "ToolsUsers", database_permissions=False)
        store.initialize_from_legacy_sheet("Users")
        self.assertEqual(len(store.load_users()), 2)
        sheets._request.assert_called_once()
        sheets.tabs["ToolsUsers"][0] = ["unexpected"]
        with self.assertRaises(database_app.DatabaseConfigError):
            database_app.GoogleSheetsUserStore(sheets, "ToolsUsers", database_permissions=False).initialize_from_legacy_sheet("Users")

    def test_invalid_legacy_headers_and_identical_source_target_are_rejected(self):
        sheets = self.migration_sheets()
        sheets.tabs["Users"][0] = ["wrong-schema"]
        for target in ("ToolsUsers", "users", "Users"):
            store = database_app.GoogleSheetsUserStore(sheets, target, database_permissions=False)
            with self.assertRaises(database_app.DatabaseConfigError):
                store.initialize_from_legacy_sheet("Users")
        sheets._request.assert_not_called()

    def test_tools_builder_uses_separate_tab_with_existing_auth_spreadsheet_settings(self):
        sheets = self.migration_sheets()
        with patch.object(tools_app, "TOOLS_AUTH_USER_STORE", None), \
             patch.object(tools_app, "TOOLS_AUTH_USERS_SHEET", "ToolsUsers"), \
             patch.object(database_app, "GoogleSheetsStore", return_value=sheets) as factory, \
             patch.dict("os.environ", {"AUTH_SPREADSHEET_ID": "same-wwa-spreadsheet", "AUTH_SERVICE_ACCOUNT_JSON": "test-secret"}):
            store = tools_app.build_tools_user_store()
            self.assertEqual(store.users_sheet, "ToolsUsers")
            self.assertFalse(store.database_permissions)
            self.assertIs(tools_app.build_tools_user_store(), store)
            self.assertEqual(factory.call_args.kwargs["spreadsheet_id"], "same-wwa-spreadsheet")
            self.assertEqual(factory.call_args.kwargs["service_account_json"], "test-secret")
        for reserved in (database_app.AUTH_USERS_SHEET, database_app.APPS_SHEET, database_app.S_APPS_SHEET, database_app.LOG_SHEET):
            with patch.object(tools_app, "TOOLS_AUTH_USER_STORE", None), \
                 patch.object(tools_app, "TOOLS_AUTH_USERS_SHEET", reserved.upper()):
                with self.assertRaises(database_app.DatabaseConfigError):
                    tools_app.build_tools_user_store()

    def test_plain_tools_store_writes_only_its_own_five_columns(self):
        sheets = self.migration_sheets(existing_target=True)
        sheets.tabs["ToolsUsers"].append(["employee@wildwildgroup.com", "hash", "TRUE", "created", "login"])
        store = database_app.GoogleSheetsUserStore(sheets, "ToolsUsers", database_permissions=False)
        store.initialize_from_legacy_sheet("Users")
        user = store.create_user("new@wildwildgroup.com", "test-hash")
        self.assertNotIn("database_access", user)
        self.assertEqual(sheets.append_values.call_args.args[0], "ToolsUsers")
        self.assertEqual(len(sheets.append_values.call_args.args[1][0]), 5)
        store.update_user("employee@wildwildgroup.com", password_hash="new-hash", active=0)
        self.assertEqual([call.args[:2] for call in sheets.update_values.call_args_list], [("ToolsUsers", "B2:B2"), ("ToolsUsers", "C2:C2")])
        with self.assertRaises(ValueError):
            store.update_user("employee@wildwildgroup.com", database_access="s")
        store.update_last_login("employee@wildwildgroup.com", "now")
        sheets.update_values.assert_called_with("ToolsUsers", "E2:E2", [["now"]])
        store.delete_user("employee@wildwildgroup.com")
        sheets.clear_value_ranges.assert_called_once_with("ToolsUsers", ["A2:E2"])

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
