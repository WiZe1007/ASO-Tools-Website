from __future__ import annotations

import base64
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

import requests
from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.auth.exceptions import GoogleAuthError
from google.oauth2 import service_account
from werkzeug.security import generate_password_hash

from account_access import (
    LoginAttemptLimiter, account_database_access, account_session_token, account_session_valid,
    account_validation_error, apply_security_headers, create_accounts_blueprint,
    hosted_runtime_detected, is_account_admin, login_csrf_token, login_limit_identity,
    request_origin_allowed,
    valid_form_csrf, verify_account_password,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_DIR = Path(__file__).resolve().parent

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(PROJECT_ROOT / "static"),
    static_url_path="/static",
)
app.config.update(
    SECRET_KEY=os.environ.get("DATABASE_SITE_SECRET_KEY") or os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    MAX_CONTENT_LENGTH=64 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_NAME="wwa_database_session",
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(
        hosted_runtime_detected()
        or os.environ.get("DATABASE_SITE_SECURE_COOKIES", "1") == "1"
    ),
    AUTH_ADMIN_EMAILS=os.environ.get("AUTH_ADMIN_EMAILS") or os.environ.get("DATABASE_SITE_ADMIN_EMAILS") or "",
    AUTH_ACCOUNT_REALM="database",
)

AUTH_REQUIRED = hosted_runtime_detected() or os.environ.get("DATABASE_SITE_AUTH_REQUIRED", "1") != "0"
AUTH_ALLOWED_EMAIL_DOMAIN = (
    os.environ.get("DATABASE_SITE_ALLOWED_EMAIL_DOMAIN")
    or os.environ.get("AUTH_ALLOWED_EMAIL_DOMAIN")
    or "@wildwildgroup.com"
).strip().lower()
AUTH_DB_PATH = Path(
    os.path.expanduser(
        os.environ.get("DATABASE_SITE_AUTH_DB")
        or str(PROJECT_ROOT / "instance" / "database-site-users.db")
    )
)
AUTH_STORAGE = (os.environ.get("DATABASE_SITE_AUTH_STORAGE") or "google_sheets").strip().lower()
AUTH_USERS_SHEET = (os.environ.get("AUTH_USERS_SHEET") or os.environ.get("DATABASE_SITE_USERS_SHEET") or "Users").strip()
AUTH_CACHE_TTL_SECONDS = max(
    5,
    int(os.environ.get("DATABASE_SITE_AUTH_CACHE_TTL_SECONDS") or "60"),
)

SPREADSHEET_ID = (
    os.environ.get("DATABASE_SITE_SPREADSHEET_ID")
    or os.environ.get("AVAILABILITY_DB_SPREADSHEET_ID")
    or os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID")
    or ""
).strip()
APPS_SHEET = (os.environ.get("DATABASE_SITE_APPS_SHEET") or "Apps").strip()
LOG_SHEET = (os.environ.get("DATABASE_SITE_LOG_SHEET") or "Checks").strip()
SERVICE_ACCOUNT_JSON = (
    os.environ.get("DATABASE_SITE_SERVICE_ACCOUNT_JSON")
    or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    or os.environ.get("GOOGLE_SERVICE_ACCOUNT_INFO")
    or ""
).strip()
SERVICE_ACCOUNT_FILE = (
    os.environ.get("DATABASE_SITE_SERVICE_ACCOUNT_FILE")
    or os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    or ""
).strip()

S_SPREADSHEET_ID = (
    os.environ.get("DATABASE_SITE_S_SPREADSHEET_ID")
    or os.environ.get("S_AVAILABILITY_DB_SPREADSHEET_ID")
    or os.environ.get("S_GOOGLE_SHEETS_SPREADSHEET_ID")
    or ""
).strip()
S_APPS_SHEET = (os.environ.get("DATABASE_SITE_S_APPS_SHEET") or "Apps").strip()
S_LOG_SHEET = (os.environ.get("DATABASE_SITE_S_LOG_SHEET") or "Checks").strip()
S_SERVICE_ACCOUNT_JSON = (
    os.environ.get("DATABASE_SITE_S_SERVICE_ACCOUNT_JSON")
    or os.environ.get("S_GOOGLE_SERVICE_ACCOUNT_JSON")
    or SERVICE_ACCOUNT_JSON
).strip()
S_SERVICE_ACCOUNT_FILE = (
    os.environ.get("DATABASE_SITE_S_SERVICE_ACCOUNT_FILE")
    or os.environ.get("S_GOOGLE_SERVICE_ACCOUNT_FILE")
    or SERVICE_ACCOUNT_FILE
).strip()
S_ALLOWED_EMAILS = (
    os.environ.get("DATABASE_SITE_S_ALLOWED_EMAILS")
    or os.environ.get("S_LIVE_DB_ALLOWED_EMAILS")
    or ""
).strip()

APPS_SHEET_HEADERS = [
    "enabled",
    "status",
    "app_url",
    "app_id",
    "app_name",
    "owner",
    "notes",
    "last_checked_at",
    "last_live_at",
    "last_open_countries",
    "last_closed_countries",
    "last_closed_count",
    "last_error",
    "app_type",
    "last_store_version",
    "last_design_fingerprint",
    "pending_store_version",
    "pending_design_fingerprint",
]
LEGACY_APPS_SHEET_HEADERS = APPS_SHEET_HEADERS[:13]
APP_TYPE_APPS_SHEET_HEADERS = APPS_SHEET_HEADERS[:14]
PREVIOUS_APPS_SHEET_HEADERS = APPS_SHEET_HEADERS[:16]

CHECKS_SHEET_HEADERS = [
    "created_at",
    "event",
    "app_id",
    "app_name",
    "app_url",
    "countries_count",
    "countries",
    "details",
]

USERS_SHEET_HEADERS = [
    "email",
    "password_hash",
    "active",
    "created_at",
    "last_login_at",
    "database_access",
]
LEGACY_USERS_SHEET_HEADERS = USERS_SHEET_HEADERS[:5]

ALLOWED_STATUSES = {"watch", "live", "banned", "paused"}
APP_TYPE_LABELS = {
    "placeholder": "Заглушка",
    "full": "Повноцінна",
}
ALLOWED_APP_TYPES = set(APP_TYPE_LABELS)
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "WWA-Apps-Database/1.0"})
AUTH_DB_LOCK = threading.Lock()
AUTH_CACHE_LOCK = threading.Lock()
AUTH_STORE_LOCK = threading.Lock()
SHEET_WRITE_LOCK = threading.Lock()
AUTH_USER_CACHE: dict[str, tuple[float, dict | None]] = {}
AUTH_USER_STORE = None
LOGIN_ATTEMPT_LIMITER = LoginAttemptLimiter()


class DatabaseConfigError(RuntimeError):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def boolish(value, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in {"0", "false", "no", "off", "disabled", "pause", "paused", "ні"}


def split_country_codes(value) -> list[str]:
    return sorted({
        part.upper()
        for part in re.split(r"[,;\s]+", str(value or ""))
        if re.fullmatch(r"[A-Za-z]{2}", part)
    })


def normalize_package_input(raw_value: str) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        try:
            parsed = urlparse(value)
            host = str(parsed.hostname or "").rstrip(".").lower()
            port = parsed.port
        except ValueError:
            return ""
        if (
            parsed.scheme != "https"
            or host not in {"play.google.com", "www.play.google.com"}
            or port not in {None, 443}
            or parsed.username
            or parsed.password
        ):
            return ""
        query_id = (parse_qs(parsed.query).get("id") or [""])[0].strip()
        if query_id:
            value = query_id
        else:
            path_parts = [part for part in parsed.path.split("/") if part]
            value = path_parts[-1] if path_parts else ""
    value = value.strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", value):
        return ""
    return value


def google_play_url(app_id: str) -> str:
    return "https://play.google.com/store/apps/details?" + urlencode({"id": app_id})


def clean_text(value, max_length: int) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or "")).strip()
    return text[:max_length]


def normalize_app_type(value, default: str = "") -> str:
    text = clean_text(value, 40).lower()
    if not text:
        return default
    aliases = {
        "placeholder": "placeholder",
        "stub": "placeholder",
        "заглушка": "placeholder",
        "full": "full",
        "full_app": "full",
        "повноцінна": "full",
        "повноцінний": "full",
    }
    return aliases.get(text, "")


def decode_service_account_info(raw: str, file_path: str, variable_name: str) -> dict:
    if raw:
        try:
            if raw.startswith("{"):
                return json.loads(raw)
            return json.loads(base64.b64decode(raw).decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as exc:
            raise DatabaseConfigError(f"Некоректний {variable_name}.") from exc
    if file_path:
        try:
            with open(os.path.expanduser(file_path), "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise DatabaseConfigError("Не вдалося прочитати service account JSON файл.") from exc
    raise DatabaseConfigError(f"Не задано {variable_name}.")


def sheet_range(sheet_name: str, cell_range: str) -> str:
    return f"'{str(sheet_name).replace(chr(39), chr(39) * 2)}'!{cell_range}"


class GoogleSheetsStore:
    def __init__(
        self,
        spreadsheet_id: str = SPREADSHEET_ID,
        apps_sheet: str = APPS_SHEET,
        log_sheet: str = LOG_SHEET,
        service_account_json: str = SERVICE_ACCOUNT_JSON,
        service_account_file: str = SERVICE_ACCOUNT_FILE,
        database_key: str = "wwa",
    ):
        self.spreadsheet_id = spreadsheet_id
        self.apps_sheet = apps_sheet
        self.log_sheet = log_sheet
        self.service_account_json = service_account_json
        self.service_account_file = service_account_file
        self.database_key = database_key
        self._credentials = None

    def _token(self) -> str:
        if not self.spreadsheet_id:
            variable_name = "DATABASE_SITE_S_SPREADSHEET_ID" if self.database_key == "s" else "DATABASE_SITE_SPREADSHEET_ID"
            raise DatabaseConfigError(f"Не задано {variable_name}.")
        try:
            if self._credentials is None:
                self._credentials = service_account.Credentials.from_service_account_info(
                    decode_service_account_info(
                        self.service_account_json,
                        self.service_account_file,
                        "DATABASE_SITE_S_SERVICE_ACCOUNT_JSON" if self.database_key == "s" else "DATABASE_SITE_SERVICE_ACCOUNT_JSON",
                    ),
                    scopes=["https://www.googleapis.com/auth/spreadsheets"],
                )
            if not self._credentials.valid:
                self._credentials.refresh(GoogleAuthRequest())
        except (ValueError, GoogleAuthError) as exc:
            raise DatabaseConfigError("Не вдалося авторизуватися в Google Sheets.") from exc
        return self._credentials.token

    def _request(self, method: str, path: str, **kwargs):
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}{path}"
        headers = dict(kwargs.pop("headers", {}))
        headers.update({"Authorization": f"Bearer {self._token()}", "Accept": "application/json"})
        last_error = None
        for attempt in range(1, 4):
            try:
                response = HTTP.request(method, url, headers=headers, timeout=(8, 35), **kwargs)
            except requests.RequestException as exc:
                last_error = exc
                if attempt == 3:
                    raise DatabaseConfigError(f"Google Sheets недоступний: {exc}") from exc
                time.sleep(0.6 * attempt)
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(0.6 * attempt)
                continue
            if response.status_code >= 400:
                raise DatabaseConfigError(f"Google Sheets HTTP {response.status_code}: {response.text[:300]}")
            return response.json() if response.text else {}
        raise DatabaseConfigError(f"Google Sheets недоступний: {last_error}")

    @staticmethod
    def _values_path(range_name: str, suffix: str = "") -> str:
        return f"/values/{quote(range_name, safe='!:\'')}{suffix}"

    def get_values(self, sheet_name: str, cell_range: str) -> list[list]:
        data = self._request("GET", self._values_path(sheet_range(sheet_name, cell_range)))
        return data.get("values") or []

    def update_values(self, sheet_name: str, cell_range: str, values: list[list]):
        self._request(
            "PUT",
            self._values_path(sheet_range(sheet_name, cell_range), "?valueInputOption=RAW"),
            json={"values": values},
        )

    def clear_value_ranges(self, sheet_name: str, cell_ranges: list[str]):
        self._request("POST", "/values:batchClear", json={
            "ranges": [sheet_range(sheet_name, cells) for cells in cell_ranges],
        })

    def append_values(self, sheet_name: str, values: list[list]):
        self._request(
            "POST",
            self._values_path(
                sheet_range(sheet_name, "A:Z"),
                ":append?valueInputOption=RAW&insertDataOption=INSERT_ROWS",
            ),
            json={"values": values},
        )

    def get_sheet_titles(self) -> set[str]:
        data = self._request("GET", "?fields=sheets.properties.title")
        return {
            str(((item or {}).get("properties") or {}).get("title") or "")
            for item in data.get("sheets", [])
        }

    def add_sheet(self, title: str):
        self._request("POST", ":batchUpdate", json={
            "requests": [{"addSheet": {"properties": {"title": title}}}],
        })

    def ensure_ready(self):
        titles = self.get_sheet_titles()
        if self.apps_sheet not in titles:
            self.add_sheet(self.apps_sheet)
        if self.log_sheet not in titles:
            self.add_sheet(self.log_sheet)
        app_headers = self.get_values(self.apps_sheet, "A1:R1")
        if not app_headers:
            self.update_values(self.apps_sheet, "A1:R1", [APPS_SHEET_HEADERS])
        else:
            normalized_headers = [str(item).strip() for item in app_headers[0]]
            if normalized_headers == LEGACY_APPS_SHEET_HEADERS:
                self.update_values(
                    self.apps_sheet,
                    "N1:R1",
                    [[
                        "app_type",
                        "last_store_version",
                        "last_design_fingerprint",
                        "pending_store_version",
                        "pending_design_fingerprint",
                    ]],
                )
            elif normalized_headers == APP_TYPE_APPS_SHEET_HEADERS:
                self.update_values(
                    self.apps_sheet,
                    "O1:R1",
                    [[
                        "last_store_version",
                        "last_design_fingerprint",
                        "pending_store_version",
                        "pending_design_fingerprint",
                    ]],
                )
            elif normalized_headers == PREVIOUS_APPS_SHEET_HEADERS:
                self.update_values(
                    self.apps_sheet,
                    "Q1:R1",
                    [["pending_store_version", "pending_design_fingerprint"]],
                )
            elif normalized_headers != APPS_SHEET_HEADERS:
                raise DatabaseConfigError("Заголовки аркуша Apps не відповідають очікуваній схемі A:R.")
        log_headers = self.get_values(self.log_sheet, "A1:H1")
        if not log_headers:
            self.update_values(self.log_sheet, "A1:H1", [CHECKS_SHEET_HEADERS])

    def load_all_apps(self) -> list[dict]:
        self.ensure_ready()
        rows = self.get_values(self.apps_sheet, "A2:R")
        apps = []
        for row_index, row in enumerate(rows, start=2):
            item = {
                header: row[index] if index < len(row) else ""
                for index, header in enumerate(APPS_SHEET_HEADERS)
            }
            app_id = normalize_package_input(item.get("app_id") or item.get("app_url"))
            if not app_id and not str(item.get("app_url") or "").strip():
                continue
            item.update({
                "row_index": row_index,
                "app_id": app_id,
                "app_url": str(item.get("app_url") or google_play_url(app_id)).strip(),
                "app_name": str(item.get("app_name") or app_id).strip(),
                "enabled": boolish(item.get("enabled"), default=True),
                "status": str(item.get("status") or "watch").strip().lower(),
                "app_type": normalize_app_type(item.get("app_type")),
            })
            apps.append(item)
        return apps

    def append_app(self, app_data: dict):
        self.append_values(self.apps_sheet, [[app_data.get(header, "") for header in APPS_SHEET_HEADERS]])

    def update_app(self, row_index: int, current: dict, updates: dict):
        row = dict(current)
        row.update(updates)
        values = [row.get(header, "") for header in APPS_SHEET_HEADERS]
        self.update_values(self.apps_sheet, f"A{row_index}:R{row_index}", [values])

    def append_log(self, event: str, app_data: dict, details: str):
        countries = split_country_codes(app_data.get("last_closed_countries"))
        self.append_values(self.log_sheet, [[
            utc_now_iso(),
            event,
            app_data.get("app_id") or "",
            app_data.get("app_name") or "",
            app_data.get("app_url") or "",
            len(countries),
            ",".join(countries),
            details[:1500],
        ]])


class GoogleSheetsUserStore:
    def __init__(self, sheets_store: GoogleSheetsStore, users_sheet: str = AUTH_USERS_SHEET, *, database_permissions=True):
        self.sheets_store = sheets_store
        self.users_sheet = users_sheet
        self.database_permissions = database_permissions
        self.headers = USERS_SHEET_HEADERS if database_permissions else LEGACY_USERS_SHEET_HEADERS
        self.last_column = "F" if database_permissions else "E"
        self._ready = False

    def initialize_from_legacy_sheet(self, source_sheet: str):
        if self.users_sheet.casefold() == source_sheet.casefold():
            raise DatabaseConfigError("Сховища користувачів двох сайтів мають бути різними.")
        titles = self.sheets_store.get_sheet_titles()
        if self.users_sheet in titles:
            self.ensure_ready()
            return
        source_rows = self.sheets_store.get_values(source_sheet, "A1:E") if source_sheet in titles else []
        if source_rows and [str(value).strip() for value in source_rows[0]] != LEGACY_USERS_SHEET_HEADERS:
            raise DatabaseConfigError("Не вдалося перевірити структуру попереднього сховища користувачів.")
        rows = [list(self.headers)]
        for source_row in source_rows[1:]:
            if not source_row or not normalize_email(source_row[0]):
                continue
            row = list(source_row[:5]) + [""] * max(0, 5 - len(source_row))
            if self.database_permissions:
                row.append("")
            rows.append(row)
        sheet_id = secrets.randbelow(2**31 - 1) + 1
        # Atomically create and seed once. Never merge accounts into an existing sheet.
        try:
            self.sheets_store._request("POST", ":batchUpdate", json={
                "requests": [
                    {"addSheet": {"properties": {"sheetId": sheet_id, "title": self.users_sheet,
                                                  "gridProperties": {"rowCount": max(1000, len(rows)), "columnCount": 26}}}},
                    {"updateCells": {
                        "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": 0},
                        "rows": [{"values": [{"userEnteredValue": {"stringValue": str(value) if value is not None else ""}}
                                             for value in row]} for row in rows],
                        "fields": "userEnteredValue",
                    }},
                ],
            })
        except DatabaseConfigError:
            # A retry or another worker may already have completed the atomic copy.
            if self.users_sheet not in self.sheets_store.get_sheet_titles():
                raise
        self.ensure_ready()

    def ensure_ready(self):
        if self._ready:
            return
        titles = self.sheets_store.get_sheet_titles()
        if self.users_sheet not in titles:
            self.sheets_store.add_sheet(self.users_sheet)
        headers = self.sheets_store.get_values(self.users_sheet, f"A1:{self.last_column}1")
        if not headers:
            self.sheets_store.update_values(self.users_sheet, f"A1:{self.last_column}1", [self.headers])
        elif self.database_permissions and [str(item).strip() for item in headers[0]] in (LEGACY_USERS_SHEET_HEADERS, LEGACY_USERS_SHEET_HEADERS + [""]):
            self.sheets_store.update_values(self.users_sheet, "F1:F1", [["database_access"]])
        elif [str(item).strip() for item in headers[0]] != self.headers:
            raise DatabaseConfigError(
                f"Заголовки аркуша {self.users_sheet} не відповідають очікуваній схемі A:{self.last_column}."
            )
        self._ready = True

    def load_users(self) -> list[dict]:
        self.ensure_ready()
        rows = self.sheets_store.get_values(self.users_sheet, f"A2:{self.last_column}")
        users = []
        for row in rows:
            item = {
                header: row[index] if index < len(row) else ""
                for index, header in enumerate(self.headers)
            }
            email = normalize_email(item.get("email"))
            if not email:
                continue
            item.update({
                "id": email,
                "email": email,
                "active": 1 if boolish(item.get("active"), default=True) else 0,
            })
            users.append(item)
        return users

    def get_user(self, identifier) -> dict | None:
        normalized = normalize_email(identifier)
        return next((user for user in self.load_users() if user["email"] == normalized), None)

    def create_user(self, email: str, password_hash: str, *, database_access="wwa") -> dict:
        normalized = normalize_email(email)
        created_at = utc_now_iso()
        with AUTH_DB_LOCK:
            if self.get_user(normalized):
                raise ValueError("USER_ALREADY_EXISTS")
            values = [
                normalized,
                password_hash,
                "TRUE",
                created_at,
                "",
            ]
            if self.database_permissions:
                values.append(database_access)
            self.sheets_store.append_values(self.users_sheet, [values])
        return {
            "id": normalized,
            "email": normalized,
            "password_hash": password_hash,
            "active": 1,
            "created_at": created_at,
            "last_login_at": "",
            **({"database_access": database_access} if self.database_permissions else {}),
        }

    def update_last_login(self, email: str, last_login_at: str):
        normalized = normalize_email(email)
        with AUTH_DB_LOCK:
            self.ensure_ready()
            rows = self.sheets_store.get_values(self.users_sheet, "A2:A")
            for row_index, row in enumerate(rows, start=2):
                if row and normalize_email(row[0]) == normalized:
                    self.sheets_store.update_values(
                        self.users_sheet,
                        f"E{row_index}:E{row_index}",
                        [[last_login_at]],
                    )
                    return

    def update_user(self, email: str, *, password_hash=None, active=None, database_access=None):
        if database_access is not None and not self.database_permissions:
            raise ValueError("DATABASE_PERMISSIONS_NOT_SUPPORTED")
        normalized = normalize_email(email)
        with AUTH_DB_LOCK:
            self.ensure_ready()
            rows = self.sheets_store.get_values(self.users_sheet, f"A2:{self.last_column}")
            for row_index, row in enumerate(rows, start=2):
                if not row or normalize_email(row[0]) != normalized:
                    continue
                # Touch only requested columns, preserving concurrent login/status updates.
                if password_hash is not None:
                    self.sheets_store.update_values(self.users_sheet, f"B{row_index}:B{row_index}", [[password_hash]])
                if active is not None:
                    self.sheets_store.update_values(self.users_sheet, f"C{row_index}:C{row_index}", [["TRUE" if active else "FALSE"]])
                if database_access is not None:
                    self.sheets_store.update_values(self.users_sheet, f"F{row_index}:F{row_index}", [[database_access]])
                return self.get_user(normalized)
        raise ValueError("USER_NOT_FOUND")

    def delete_user(self, email: str):
        normalized = normalize_email(email)
        if not normalized:
            raise ValueError("USER_NOT_FOUND")
        with AUTH_DB_LOCK:
            self.ensure_ready()
            rows = self.sheets_store.get_values(self.users_sheet, "A2:A")
            ranges = [
                f"A{index}:{self.last_column}{index}" for index, row in enumerate(rows, start=2)
                if row and normalize_email(row[0]) == normalized
            ]
            if not ranges:
                raise ValueError("USER_NOT_FOUND")
            # Erase account data without shifting other users' row addresses.
            self.sheets_store.clear_value_ranges(self.users_sheet, ranges)


@contextmanager
def auth_db_connect():
    AUTH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(AUTH_DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def ensure_auth_db():
    with AUTH_DB_LOCK, auth_db_connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_login_at TEXT
            )
            """
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
        if "database_access" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN database_access TEXT")


def normalize_email(value: str) -> str:
    return str(value or "").strip().lower()


def parse_email_allowlist(value: str) -> set[str]:
    return {
        normalize_email(part)
        for part in re.split(r"[\s,;]+", str(value or ""))
        if normalize_email(part)
    }


def user_can_access_database(database_key: str, email: str | None = None) -> bool:
    user = getattr(g, "current_user", None)
    if not AUTH_REQUIRED:
        user = {"email": normalize_email(email or current_email())}
    return database_key in account_database_access(user, S_ALLOWED_EMAILS)


def database_options(email: str | None = None) -> list[dict]:
    options = []
    if user_can_access_database("wwa", email):
        options.append({
            "key": "wwa",
            "label": "WWA DB",
            "title": "WWA Apps Database",
            "configured": bool(SPREADSHEET_ID and (SERVICE_ACCOUNT_JSON or SERVICE_ACCOUNT_FILE)),
        })
    if user_can_access_database("s", email):
        options.append({
            "key": "s",
            "label": "S DB",
            "title": "S Apps Database",
            "configured": bool(S_SPREADSHEET_ID and (S_SERVICE_ACCOUNT_JSON or S_SERVICE_ACCOUNT_FILE)),
        })
    return options


def build_store(database_key: str) -> GoogleSheetsStore:
    if database_key == "wwa":
        return GoogleSheetsStore(
            spreadsheet_id=SPREADSHEET_ID,
            apps_sheet=APPS_SHEET,
            log_sheet=LOG_SHEET,
            service_account_json=SERVICE_ACCOUNT_JSON,
            service_account_file=SERVICE_ACCOUNT_FILE,
            database_key="wwa",
        )
    if database_key == "s":
        return GoogleSheetsStore(
            spreadsheet_id=S_SPREADSHEET_ID,
            apps_sheet=S_APPS_SHEET,
            log_sheet=S_LOG_SHEET,
            service_account_json=S_SERVICE_ACCOUNT_JSON,
            service_account_file=S_SERVICE_ACCOUNT_FILE,
            database_key="s",
        )
    raise DatabaseConfigError("Невідома база даних.")


def auth_uses_google_sheets() -> bool:
    if AUTH_STORAGE not in {"google_sheets", "sqlite"}:
        raise DatabaseConfigError(
            "DATABASE_SITE_AUTH_STORAGE має бути google_sheets або sqlite."
        )
    return AUTH_STORAGE == "google_sheets"


def build_user_store() -> GoogleSheetsUserStore:
    global AUTH_USER_STORE
    with AUTH_STORE_LOCK:
        if AUTH_USER_STORE is None:
            sheets = GoogleSheetsStore(
                spreadsheet_id=os.environ.get("AUTH_SPREADSHEET_ID") or SPREADSHEET_ID,
                service_account_json=os.environ.get("AUTH_SERVICE_ACCOUNT_JSON") or SERVICE_ACCOUNT_JSON,
                service_account_file=os.environ.get("AUTH_SERVICE_ACCOUNT_FILE") or SERVICE_ACCOUNT_FILE,
            )
            AUTH_USER_STORE = GoogleSheetsUserStore(sheets, AUTH_USERS_SHEET)
        return AUTH_USER_STORE


def get_cached_user(identifier) -> tuple[bool, dict | None]:
    key = normalize_email(identifier)
    if not key:
        return True, None
    with AUTH_CACHE_LOCK:
        cached = AUTH_USER_CACHE.get(key)
        if not cached or cached[0] <= time.monotonic():
            AUTH_USER_CACHE.pop(key, None)
            return False, None
        return True, dict(cached[1]) if cached[1] else None


def cache_user(identifier, user: dict | None):
    key = normalize_email(identifier)
    if not key:
        return
    cached_user = dict(user) if user else None
    with AUTH_CACHE_LOCK:
        AUTH_USER_CACHE[key] = (time.monotonic() + AUTH_CACHE_TTL_SECONDS, cached_user)


def clear_cached_user(identifier):
    key = normalize_email(identifier)
    with AUTH_CACHE_LOCK:
        AUTH_USER_CACHE.pop(key, None)


def get_sheets_user(email: str, fresh=False):
    normalized = normalize_email(email)
    if not fresh:
        found, user = get_cached_user(normalized)
        if found:
            return user
    user = build_user_store().get_user(normalized)
    cache_user(normalized, user)
    return user


def get_user_by_email(email: str, fresh=False):
    normalized = normalize_email(email)
    if auth_uses_google_sheets():
        return get_sheets_user(normalized, fresh=fresh)
    ensure_auth_db()
    with auth_db_connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE email = ?", (normalized,)).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id, fresh=False):
    if not user_id:
        return None
    if auth_uses_google_sheets():
        return get_user_by_email(user_id, fresh=fresh)
    ensure_auth_db()
    with auth_db_connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def create_user(email: str, password: str, *, database_access="wwa"):
    normalized = normalize_email(email)
    error = account_validation_error(normalized, password, AUTH_ALLOWED_EMAIL_DOMAIN)
    if error:
        raise ValueError(error)
    password_hash = generate_password_hash(password)
    if auth_uses_google_sheets():
        user = build_user_store().create_user(normalized, password_hash, database_access=database_access)
        cache_user(normalized, user)
        return user
    ensure_auth_db()
    try:
        with AUTH_DB_LOCK, auth_db_connect() as connection:
            connection.execute(
                "INSERT INTO users (email, password_hash, created_at, database_access) VALUES (?, ?, ?, ?)",
                (normalized, password_hash, utc_now_iso(), database_access),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError("USER_ALREADY_EXISTS") from exc
    return get_user_by_email(normalized)


def list_users():
    if auth_uses_google_sheets():
        return build_user_store().load_users()
    ensure_auth_db()
    with auth_db_connect() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM users ORDER BY email")]


def update_user(email: str, *, password=None, active=None, database_access=None):
    normalized = normalize_email(email)
    password_hash = generate_password_hash(password) if password is not None else None
    if auth_uses_google_sheets():
        try:
            return build_user_store().update_user(normalized, password_hash=password_hash, active=active, database_access=database_access)
        finally:
            clear_cached_user(normalized)
    ensure_auth_db()
    with AUTH_DB_LOCK, auth_db_connect() as connection:
        changed = connection.execute(
            "UPDATE users SET password_hash = COALESCE(?, password_hash), active = COALESCE(?, active), database_access = COALESCE(?, database_access) WHERE email = ?",
            (password_hash, active, database_access, normalized),
        )
        if not changed.rowcount:
            raise ValueError("USER_NOT_FOUND")
    return get_user_by_email(normalized)


def delete_user(email: str):
    normalized = normalize_email(email)
    if auth_uses_google_sheets():
        try:
            build_user_store().delete_user(normalized)
        finally:
            clear_cached_user(normalized)
        return
    ensure_auth_db()
    with AUTH_DB_LOCK, auth_db_connect() as connection:
        deleted = connection.execute("DELETE FROM users WHERE email = ?", (normalized,))
        if not deleted.rowcount:
            raise ValueError("USER_NOT_FOUND")


def login_user(user: dict):
    session.clear()
    session["user_id"] = user["id"]
    session["user_email"] = user["email"]
    session["csrf_token"] = secrets.token_urlsafe(24)
    session["account_token"] = account_session_token(user)
    if auth_uses_google_sheets():
        last_login_at = utc_now_iso()
        updated_user = dict(user)
        updated_user["last_login_at"] = last_login_at
        cache_user(user["email"], updated_user)
        try:
            build_user_store().update_last_login(user["email"], last_login_at)
        except DatabaseConfigError:
            pass
        return
    with AUTH_DB_LOCK, auth_db_connect() as connection:
        connection.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (utc_now_iso(), user["id"]))


def current_email() -> str:
    return normalize_email(session.get("user_email") or "local@wildwildgroup.com")


def api_error(message: str, status: int = 400, code: str = "BAD_REQUEST"):
    return jsonify({"ok": False, "error": code, "message": message}), status


def require_csrf():
    expected = str(session.get("csrf_token") or "")
    supplied = str(request.headers.get("X-CSRF-Token") or "")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        return api_error("Сесію форми втрачено. Онови сторінку та спробуй ще раз.", 403, "CSRF_FAILED")
    return None


def require_database_access(database_key: str):
    if database_key not in {"wwa", "s"}:
        return api_error("Базу даних не знайдено.", 404, "DATABASE_NOT_FOUND")
    if not user_can_access_database(database_key):
        return api_error(f"У тебе немає доступу до {'WWA DB' if database_key == 'wwa' else 'S DB'}.", 403, "DATABASE_FORBIDDEN")
    return None


def app_payload(item: dict) -> dict:
    closed = split_country_codes(item.get("last_closed_countries"))
    opened = split_country_codes(item.get("last_open_countries"))
    app_type = normalize_app_type(item.get("app_type"))
    app_id = normalize_package_input(item.get("app_id") or item.get("app_url") or "")
    return {
        "row_index": int(item.get("row_index") or 0),
        "enabled": boolish(item.get("enabled"), default=True),
        "status": str(item.get("status") or "watch").strip().lower(),
        "app_url": google_play_url(app_id) if app_id else "",
        "app_id": app_id,
        "app_name": str(item.get("app_name") or item.get("app_id") or ""),
        "owner": str(item.get("owner") or ""),
        "notes": str(item.get("notes") or ""),
        "last_checked_at": str(item.get("last_checked_at") or ""),
        "last_live_at": str(item.get("last_live_at") or ""),
        "open_countries": opened,
        "closed_countries": closed,
        "closed_count": len(closed),
        "last_error": str(item.get("last_error") or ""),
        "app_type": app_type,
        "app_type_label": APP_TYPE_LABELS.get(app_type, "Не вказано"),
    }


@app.before_request
def require_authentication():
    g.current_user = None
    if request.endpoint is None:
        return None
    if request.endpoint == "logout" and not request_origin_allowed():
        return "Заборонений міжсайтовий запит.", 403
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not request_origin_allowed():
        if request.path.startswith("/api/") or request.is_json:
            return api_error("Заборонений міжсайтовий запит.", 403, "CROSS_ORIGIN_REQUEST_BLOCKED")
        return "Заборонений міжсайтовий запит.", 403
    if not AUTH_REQUIRED:
        if not session.get("csrf_token"):
            session["csrf_token"] = secrets.token_urlsafe(24)
        return None
    if request.endpoint in {"login", "health", "static"}:
        return None
    try:
        fresh = request.endpoint in {"dashboard", "list_apps", "add_app", "update_app"}
        user = get_user_by_id(session.get("user_id"), fresh=fresh)
    except DatabaseConfigError:
        return api_error("Сховище користувачів тимчасово недоступне.", 503, "AUTH_STORAGE_UNAVAILABLE")
    if account_session_valid(user):
        g.current_user = user
        return None
    session.clear()
    if request.path.startswith("/api/"):
        return api_error("Потрібно увійти в систему.", 401, "AUTH_REQUIRED")
    return redirect(url_for("login", next=request.path))


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "wwa-apps-database"})


@app.route("/login", methods=["GET", "POST"])
def login():
    if not AUTH_REQUIRED:
        return redirect(url_for("dashboard"))
    error = ""
    status = 200
    retry_after = 0
    email = normalize_email(request.form.get("email") or "")
    if request.method == "POST":
        if not valid_form_csrf():
            return "Сесію форми втрачено. Онови сторінку.", 403
        password = request.form.get("password") or ""
        login_identity = login_limit_identity(email, request.remote_addr)
        retry_after = LOGIN_ATTEMPT_LIMITER.retry_after(login_identity)
        if retry_after:
            error, status = "Забагато невдалих спроб. Спробуй ще раз трохи пізніше.", 429
        else:
            try:
                user = get_user_by_email(email, fresh=True)
                if len(password) > 256 or not verify_account_password(user, password):
                    retry_after = LOGIN_ATTEMPT_LIMITER.record_failure(login_identity)
                    error = "Невірна корпоративна пошта або пароль."
                    if retry_after:
                        error, status = "Забагато невдалих спроб. Спробуй ще раз трохи пізніше.", 429
                else:
                    LOGIN_ATTEMPT_LIMITER.clear(login_identity)
                    login_user(user)
                    return redirect(url_for("dashboard"))
            except DatabaseConfigError:
                error, status = "Сховище користувачів тимчасово недоступне. Спробуй пізніше.", 503
    rendered = render_template("auth.html", email=email, error=error, domain=AUTH_ALLOWED_EMAIL_DOMAIN,
                               csrf_token=login_csrf_token())
    headers = {"Retry-After": str(retry_after)} if retry_after else {}
    return rendered, status, headers


@app.after_request
def add_browser_security_headers(response):
    return apply_security_headers(response)


@app.context_processor
def inject_account_context():
    return {
        "can_manage_users": is_account_admin(),
        "logout_csrf_token": login_csrf_token(),
    }


app.register_blueprint(create_accounts_blueprint(
    list_users=list_users, get_user=get_user_by_email, create_user=create_user, update_user=update_user,
    delete_user=delete_user,
    database_access=lambda user: account_database_access(user, S_ALLOWED_EMAILS),
    domain=lambda: AUTH_ALLOWED_EMAIL_DOMAIN, site_name="WWA Apps Database", home_endpoint="dashboard",
    storage_errors=(DatabaseConfigError, sqlite3.Error),
))


@app.post("/logout")
def logout():
    if not valid_form_csrf():
        return "Сесію форми втрачено. Онови сторінку.", 403
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
def dashboard():
    if not session.get("csrf_token"):
        session["csrf_token"] = secrets.token_urlsafe(24)
    databases = database_options(current_email())
    if not databases:
        return render_template("accounts/no_database_access.html", site_name="WWA Apps Database"), 403
    return render_template(
        "dashboard.html",
        csrf_token=session["csrf_token"],
        current_user_email=current_email(),
        databases=databases,
        spreadsheet_configured=databases[0]["configured"],
    )


@app.get("/api/databases/<database_key>/apps")
@app.get("/api/apps")
def list_apps(database_key: str = "wwa"):
    access_error = require_database_access(database_key)
    if access_error:
        return access_error
    try:
        items = build_store(database_key).load_all_apps()
        return jsonify({
            "ok": True,
            "database": database_key,
            "apps": [app_payload(item) for item in items],
            "updated_at": utc_now_iso(),
        })
    except DatabaseConfigError as exc:
        return api_error(str(exc), 503, "SHEETS_UNAVAILABLE")


@app.post("/api/databases/<database_key>/apps")
@app.post("/api/apps")
def add_app(database_key: str = "wwa"):
    access_error = require_database_access(database_key)
    if access_error:
        return access_error
    csrf_error = require_csrf()
    if csrf_error:
        return csrf_error
    payload = request.get_json(silent=True) or {}
    app_id = normalize_package_input(payload.get("app_input") or payload.get("app_id") or payload.get("app_url"))
    if not app_id:
        return api_error("Введи коректний package name або Google Play URL.", 422, "INVALID_APP_ID")
    status = clean_text(payload.get("status") or "watch", 20).lower()
    if status not in ALLOWED_STATUSES:
        return api_error("Некоректний статус додатка.", 422, "INVALID_STATUS")
    app_type = normalize_app_type(payload.get("app_type"), default="full")
    if app_type not in ALLOWED_APP_TYPES:
        return api_error("Обери тип додатка: Заглушка або Повноцінна.", 422, "INVALID_APP_TYPE")
    enabled = bool(payload.get("enabled", True))
    item = {
        "enabled": enabled,
        "status": status,
        "app_url": google_play_url(app_id),
        "app_id": app_id,
        "app_name": clean_text(payload.get("app_name") or app_id, 160),
        "owner": clean_text(payload.get("owner") or current_email(), 160),
        "notes": clean_text(payload.get("notes"), 1200),
        "last_checked_at": "",
        "last_live_at": "",
        "last_open_countries": "",
        "last_closed_countries": "",
        "last_closed_count": "",
        "last_error": "",
        "app_type": app_type,
    }
    store = build_store(database_key)
    try:
        with SHEET_WRITE_LOCK:
            apps = store.load_all_apps()
            if any(str(app_item.get("app_id") or "").lower() == app_id.lower() for app_item in apps):
                return api_error("Цей додаток уже є в базі.", 409, "DUPLICATE_APP")
            store.append_app(item)
            store.append_log("database_add", item, json.dumps({
                "actor": current_email(),
                "database": database_key,
                "status": status,
                "enabled": enabled,
                "app_type": app_type,
            }, ensure_ascii=False))
            refreshed = store.load_all_apps()
        created = next((row for row in reversed(refreshed) if row.get("app_id") == app_id), item)
        return jsonify({"ok": True, "database": database_key, "app": app_payload(created)}), 201
    except DatabaseConfigError as exc:
        return api_error(str(exc), 503, "SHEETS_UNAVAILABLE")


@app.patch("/api/databases/<database_key>/apps/<int:row_index>")
@app.patch("/api/apps/<int:row_index>")
def update_app(row_index: int, database_key: str = "wwa"):
    access_error = require_database_access(database_key)
    if access_error:
        return access_error
    csrf_error = require_csrf()
    if csrf_error:
        return csrf_error
    if row_index < 2:
        return api_error("Некоректний номер рядка.", 422, "INVALID_ROW")
    payload = request.get_json(silent=True) or {}
    expected_app_id = normalize_package_input(payload.get("expected_app_id"))
    updates = {}
    if "app_name" in payload:
        updates["app_name"] = clean_text(payload.get("app_name"), 160)
    if "owner" in payload:
        updates["owner"] = clean_text(payload.get("owner"), 160)
    if "notes" in payload:
        updates["notes"] = clean_text(payload.get("notes"), 1200)
    if "enabled" in payload:
        updates["enabled"] = bool(payload.get("enabled"))
    if "status" in payload:
        status = clean_text(payload.get("status"), 20).lower()
        if status not in ALLOWED_STATUSES:
            return api_error("Некоректний статус додатка.", 422, "INVALID_STATUS")
        updates["status"] = status
    if "app_type" in payload:
        app_type = normalize_app_type(payload.get("app_type"))
        if app_type not in ALLOWED_APP_TYPES:
            return api_error("Обери тип додатка: Заглушка або Повноцінна.", 422, "INVALID_APP_TYPE")
        updates["app_type"] = app_type
    if not updates:
        return api_error("Немає змін для збереження.", 422, "NO_CHANGES")

    store = build_store(database_key)
    try:
        with SHEET_WRITE_LOCK:
            apps = store.load_all_apps()
            current = next((item for item in apps if int(item.get("row_index") or 0) == row_index), None)
            if not current:
                return api_error("Запис більше не існує. Онови список.", 404, "APP_NOT_FOUND")
            if expected_app_id and expected_app_id.lower() != str(current.get("app_id") or "").lower():
                return api_error("Таблиця змінилася. Онови список перед редагуванням.", 409, "STALE_ROW")
            store.update_app(row_index, current, updates)
            changed = dict(current)
            changed.update(updates)
            event = "database_archive" if updates.get("enabled") is False else "database_update"
            store.append_log(event, changed, json.dumps({
                "actor": current_email(),
                "database": database_key,
                "changes": updates,
            }, ensure_ascii=False))
        return jsonify({"ok": True, "database": database_key, "app": app_payload(changed)})
    except DatabaseConfigError as exc:
        return api_error(str(exc), 503, "SHEETS_UNAVAILABLE")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8090"))
    app.run(host="127.0.0.1", port=port, debug=False)
