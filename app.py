import json
import time
import threading
import webbrowser
import re
import random
import os
import sys
import socket
import atexit
import copy
import base64
import io
import html as html_lib
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup
from flask import Flask, g, render_template, request, jsonify, redirect, url_for, session as flask_session
from werkzeug.security import check_password_hash, generate_password_hash

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except Exception:
    Image = None
    ImageDraw = None
    ImageFont = None
    ImageOps = None

try:
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as GoogleAuthRequest
except Exception:
    service_account = None
    GoogleAuthRequest = None

# ---------------- CONFIG ----------------

def env_int(name: str, default: int, min_value: int = 1, max_value: int = 256) -> int:
    try:
        value = int(os.environ.get(name, default))
    except Exception:
        value = default
    return max(min_value, min(max_value, value))


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off", "ні"}


DEFAULT_THRESHOLD = 4.0
HOST = "127.0.0.1"
PORT = 5000  # preferred port if free

MAX_WORKERS_GOOGLE = env_int("WWA_MAX_WORKERS_GOOGLE", 10, 4, 24)
MAX_WORKERS_APPLE = env_int("WWA_MAX_WORKERS_APPLE", 25, 8, 48)
MAX_WORKERS_APPMAGIC = env_int("WWA_MAX_WORKERS_APPMAGIC", 14, 6, 32)

# Availability checks
MAX_WORKERS_AVAIL_GOOGLE = env_int("WWA_MAX_WORKERS_AVAIL_GOOGLE", 12, 6, 28)
MAX_WORKERS_AVAIL_APPLE = env_int("WWA_MAX_WORKERS_AVAIL_APPLE", 25, 8, 48)
MAX_WORKERS_BOT_AVAILABILITY = env_int("WWA_BOT_MAX_WORKERS_AVAILABILITY", 3, 1, 8)
OVERVIEW_AVAILABILITY_CACHE_TTL = 15 * 60
CACHE_TTL_RATINGS = env_int("WWA_CACHE_TTL_RATINGS", 6 * 60 * 60, 60, 24 * 60 * 60)
CACHE_TTL_INSTALL_RANGE = env_int("WWA_CACHE_TTL_INSTALL_RANGE", 12 * 60 * 60, 60, 24 * 60 * 60)
CACHE_TTL_AVAILABILITY = env_int("WWA_CACHE_TTL_AVAILABILITY", 6 * 60 * 60, 60, 24 * 60 * 60)
CACHE_TTL_APPMAGIC = env_int("WWA_CACHE_TTL_APPMAGIC", 20 * 60, 60, 6 * 60 * 60)
CACHE_TTL_SENSOR_TOWER = env_int("WWA_CACHE_TTL_SENSOR_TOWER", 30 * 60, 60, 6 * 60 * 60)
CACHE_TTL_APP_OVERVIEW = env_int("WWA_CACHE_TTL_APP_OVERVIEW", 15 * 60, 60, 6 * 60 * 60)
CACHE_MAX_ITEMS = env_int("WWA_CACHE_MAX_ITEMS", 1200, 100, 10000)
HTTP_POOL_SIZE = env_int("WWA_HTTP_POOL_SIZE", 64, 16, 256)
HTTP_REQUEST_RETRIES = env_int("WWA_HTTP_REQUEST_RETRIES", 3, 1, 8)

GOOGLE_JITTER_MIN = 0.08
GOOGLE_JITTER_MAX = 0.35
GOOGLE_PLAY_DEFAULT_INSTALL_COUNTRY = "US"
GOOGLE_PLAY_DEFAULT_INSTALL_LANG = "en"
GOOGLE_AVAILABILITY_CLOSED_ERRORS = {"NO_INSTALL_SIGNALS", "NOT_FOUND"}
AVAILABILITY_CONFIRM_ALL_COUNTRIES = env_bool("WWA_AVAILABILITY_CONFIRM_ALL_COUNTRIES", True)

APPMAGIC_SEARCH_BY_IDS_URL = "https://appmagic.rocks/api/v2/united-applications/search-by-ids"
APPMAGIC_DATA_COUNTRIES_URL = "https://appmagic.rocks/api/v2/united-applications/data-countries"
APPMAGIC_APP_INFO_URL = "https://appmagic.rocks/api/v2/applications/app-info"
APPMAGIC_EMAIL_AUTH_URL = "https://appmagic.rocks/api/v2/auth/email"
APPMAGIC_TIMEOUT = 30
APPMAGIC_RETRIES = 2
APPMAGIC_RETRY_BASE_DELAY = 0.45
APPMAGIC_TEMPORARY_STATUS_CODES = {429, 500, 502, 503, 504}
APPMAGIC_RANK_ESTIMATE_EXPONENT = 1.5
APPMAGIC_TOP_GEO_LIMIT = 10
APPMAGIC_PLACEHOLDER_INSTALL_MAX = 1
APPMAGIC_REPAIR_MIN_EXPECTED_INSTALLS = 50
APPMAGIC_TOTAL_INFO_COUNTRIES = [
    "US", "GB", "UA", "PL", "DE",
    "ZA", "CL", "IN", "BR", "MX",
]
# When App Magic auth is unavailable, we fetch public per-country app-info for up
# to this many countries so we can rank TOP GEO by real downloads (not by the
# arbitrary order of `dataCountries`). Higher = more accurate, slightly slower.
APPMAGIC_MAX_INFO_COUNTRIES = 80
APPMAGIC_COLORS = [
    "#008ae0", "#11a02c", "#d70006", "#e6b400", "#455a64",
    "#00b8d4", "#72c903", "#c51162", "#f56a00", "#a300f5",
    "#47699b", "#7d0932", "#00bfa5", "#ffab00", "#5d4037",
]
APPMAGIC_PERIOD_FIELDS = {
    "last30days": {
        "label": "Last 30 days",
        "downloads": "Last30DaysDownloads",
        "percent": "Last30DaysDownloadsPercent",
    },
    "lifetime": {
        "label": "Lifetime",
        "downloads": "LifetimeDownloads",
        "percent": "LifetimeDownloadsPercent",
    },
}
APPMAGIC_FALLBACK_TOP_GEO_ISO2 = [
    "US", "GB", "CA", "AU", "DE",
    "FR", "BR", "MX", "JP", "KR",
]

SENSOR_TOWER_APP_URL = "https://app.sensortower.com/overview/{app_id}?country={country}"
SENSOR_TOWER_APP_API_URL = "https://app.sensortower.com/api/android/apps/{app_id}"
SENSOR_TOWER_PUBLISHER_URL = "https://app.sensortower.com/publisher/{os}/{publisher_id}"
SENSOR_TOWER_PUBLISHER_METADATA_API_URL = "https://app.sensortower.com/api/{os}/publishers/{publisher_id}/metadata"
SENSOR_TOWER_PUBLISHER_APPS_API_URL = "https://app.sensortower.com/api/{os}/publishers/{publisher_id}/apps"
SENSOR_TOWER_TIMEOUT = 30
SENSOR_TOWER_ENGLISH_DESCRIPTION_COUNTRIES = ("US", "AU", "GB", "CA", "NZ", "IE")
SENSOR_TOWER_PUBLISHER_APPS_LIMIT = 50

GOOGLE_SHEETS_SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)
AVAILABILITY_DB_SPREADSHEET_ID = (
    os.environ.get("AVAILABILITY_DB_SPREADSHEET_ID")
    or os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID")
    or ""
).strip()
AVAILABILITY_DB_APPS_SHEET = os.environ.get("AVAILABILITY_DB_APPS_SHEET", "Apps").strip() or "Apps"
AVAILABILITY_DB_LOG_SHEET = os.environ.get("AVAILABILITY_DB_LOG_SHEET", "Checks").strip() or "Checks"
AVAILABILITY_CHECK_LIMIT = env_int("AVAILABILITY_CHECK_LIMIT", 200, 1, 1000)
AVAILABILITY_TASK_SECRET = (os.environ.get("AVAILABILITY_TASK_SECRET") or os.environ.get("BOT_CHECK_SECRET") or "").strip()
TELEGRAM_BOT_TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
TELEGRAM_SEND_APP_CARD = env_bool("TELEGRAM_SEND_APP_CARD", True)
AUTH_REQUIRED = (os.environ.get("AUTH_REQUIRED", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}
AUTH_ALLOWED_EMAIL_DOMAIN = (os.environ.get("AUTH_ALLOWED_EMAIL_DOMAIN", "@wildwildgroup.com") or "@wildwildgroup.com").strip().lower()
AUTH_DB_PATH = (
    os.environ.get("AUTH_DB_PATH")
    or os.path.expanduser("~/.wwa_aso_checker_users.sqlite3")
).strip()

# Single-instance state
STATE_FILE = os.path.expanduser("~/.wwa_aso_checker_state.json")
APPMAGIC_AUTH_FILE = os.path.expanduser("~/.wwa_aso_checker_appmagic_auth.json")
APPMAGIC_TOKEN_PATTERN = re.compile(r"\b(?:Bearer\s+)?([A-Za-z0-9._~+/\-=]{20,})\b", re.I)
OVERVIEW_AVAILABILITY_CACHE: dict[str, dict] = {}
OVERVIEW_AVAILABILITY_CACHE_LOCK = threading.Lock()
CACHE_MISS = object()


class TTLCache:
    def __init__(self, ttl_seconds: int, max_items: int = CACHE_MAX_ITEMS):
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._items: dict[tuple, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple):
        now = time.time()
        with self._lock:
            item = self._items.get(key)
            if not item:
                return CACHE_MISS
            created_at, value = item
            if now - created_at > self.ttl_seconds:
                self._items.pop(key, None)
                return CACHE_MISS
            return copy.deepcopy(value)

    def set(self, key: tuple, value):
        now = time.time()
        with self._lock:
            if len(self._items) >= self.max_items:
                expired_keys = [
                    cache_key
                    for cache_key, (created_at, _value) in self._items.items()
                    if now - created_at > self.ttl_seconds
                ]
                for cache_key in expired_keys[: max(1, len(expired_keys))]:
                    self._items.pop(cache_key, None)
                if len(self._items) >= self.max_items:
                    oldest_keys = sorted(
                        self._items,
                        key=lambda cache_key: self._items[cache_key][0],
                    )[: max(1, self.max_items // 10)]
                    for cache_key in oldest_keys:
                        self._items.pop(cache_key, None)
            self._items[key] = (now, copy.deepcopy(value))


GOOGLE_RATING_CACHE = TTLCache(CACHE_TTL_RATINGS)
APPLE_RATING_CACHE = TTLCache(CACHE_TTL_RATINGS)
GOOGLE_INSTALL_RANGE_CACHE = TTLCache(CACHE_TTL_INSTALL_RANGE)
GOOGLE_AVAILABILITY_CACHE = TTLCache(CACHE_TTL_AVAILABILITY)
APPLE_AVAILABILITY_CACHE = TTLCache(CACHE_TTL_AVAILABILITY)
APPMAGIC_SEARCH_CACHE = TTLCache(CACHE_TTL_APPMAGIC)
APPMAGIC_INFO_CACHE = TTLCache(CACHE_TTL_APPMAGIC)
APPMAGIC_DATA_COUNTRIES_CACHE = TTLCache(CACHE_TTL_APPMAGIC)
SENSOR_TOWER_APP_CACHE = TTLCache(CACHE_TTL_SENSOR_TOWER)
SENSOR_TOWER_PUBLISHER_METADATA_CACHE = TTLCache(CACHE_TTL_SENSOR_TOWER)
SENSOR_TOWER_PUBLISHER_APPS_CACHE = TTLCache(CACHE_TTL_SENSOR_TOWER)
APP_OVERVIEW_PAYLOAD_CACHE = TTLCache(CACHE_TTL_APP_OVERVIEW)
PUBLISHER_PAYLOAD_CACHE = TTLCache(CACHE_TTL_SENSOR_TOWER)
TELEGRAM_APP_CARD_CACHE = TTLCache(CACHE_TTL_SENSOR_TOWER)

# ---------------- COUNTRY LISTS ----------------

COUNTRIES_TOOLBOX = {
    "Vietnam": ("VN", "vi"),
    "Ukraine": ("UA", "uk"),
    "Turkey": ("TR", "tr"),
    "Thailand": ("TH", "th"),
    "Taiwan": ("TW", "zh-TW"),
    "Sweden": ("SE", "sv"),
    "Spain": ("ES", "es-ES"),
    "Russia": ("RU", "ru"),
    "Romania": ("RO", "ro"),
    "Portugal": ("PT", "pt-PT"),
    "Poland": ("PL", "pl"),
    "Netherlands": ("NL", "nl"),
    "Montserrat": ("MS", "ms"),
    "Mexico": ("MX", "es-MX"),
    "South Korea": ("KR", "ko"),
    "Japan": ("JP", "ja"),
    "Italy": ("IT", "it"),
    "Indonesia": ("ID", "id"),
    "India": ("IN", "hi"),
    "Hungary": ("HU", "hu"),
    "Hong Kong": ("HK", "zh-HK"),
    "Finland": ("FI", "fi"),
    "Czechia": ("CZ", "cs"),
    "Croatia": ("HR", "hr"),
    "China": ("CN", "zh-CN"),
    "Canada": ("CA", "en-CA"),
    "Brazil": ("BR", "pt-BR"),
    "Australia": ("AU", "en-AU"),
    "Argentina": ("AR", "es-AR"),
    "United States": ("US", "en-US"),
    "United Kingdom": ("GB", "en-GB"),
    "Germany": ("DE", "de"),
    "France": ("FR", "fr-FR"),
}

COUNTRIES_FULL = {
    **COUNTRIES_TOOLBOX,
    "Austria": ("AT", "de"),
    "Azerbaijan": ("AZ", "az"),
    "Albania": ("AL", "sq"),
    "Algeria": ("DZ", "ar"),
    "Angola": ("AO", "pt"),
    "Andorra": ("AD", "ca"),
    "Antigua and Barbuda": ("AG", "en"),
    "Armenia": ("AM", "hy"),
    "Aruba": ("AW", "nl"),
    "Afghanistan": ("AF", "fa"),
    "Bahamas": ("BS", "en"),
    "Bangladesh": ("BD", "bn"),
    "Barbados": ("BB", "en"),
    "Bahrain": ("BH", "ar"),
    "Belize": ("BZ", "en"),
    "Belarus": ("BY", "be"),
    "Belgium": ("BE", "nl"),
    "Benin": ("BJ", "fr"),
    "Bermuda": ("BM", "en"),
    "Bulgaria": ("BG", "bg"),
    "Bolivia": ("BO", "es"),
    "Bosnia and Herzegovina": ("BA", "bs"),
    "Botswana": ("BW", "en"),
    "Brunei": ("BN", "ms"),
    "Burkina Faso": ("BF", "fr"),
    "Burundi": ("BI", "fr"),
    "Cambodia": ("KH", "km"),
    "Cameroon": ("CM", "fr"),
    "Qatar": ("QA", "ar"),
    "Kenya": ("KE", "sw"),
    "Cyprus": ("CY", "el"),
    "Colombia": ("CO", "es"),
    "Costa Rica": ("CR", "es"),
    "Côte d’Ivoire": ("CI", "fr"),
    "Cuba": ("CU", "es"),
    "Denmark": ("DK", "da"),
    "Djibouti": ("DJ", "fr"),
    "Dominica": ("DM", "en"),
    "Dominican Republic": ("DO", "es"),
    "Egypt": ("EG", "ar"),
    "Ecuador": ("EC", "es"),
    "Estonia": ("EE", "et"),
    "Eritrea": ("ER", "ti"),
    "Fiji": ("FJ", "en"),
    "Georgia": ("GE", "ka"),
    "Greece": ("GR", "el"),
    "Grenada": ("GD", "en"),
    "Guatemala": ("GT", "es"),
    "Guinea": ("GN", "fr"),
    "Guinea-Bissau": ("GW", "pt"),
    "Gabon": ("GA", "fr"),
    "Gambia": ("GM", "en"),
    "Ghana": ("GH", "en"),
    "Haiti": ("HT", "ht"),
    "Honduras": ("HN", "es"),
    "Iceland": ("IS", "is"),
    "Iran": ("IR", "fa"),
    "Iraq": ("IQ", "ar"),
    "Ireland": ("IE", "ga"),
    "Israel": ("IL", "he"),
    "Jamaica": ("JM", "en"),
    "Jordan": ("JO", "ar"),
    "Kazakhstan": ("KZ", "kk"),
    "Kuwait": ("KW", "ar"),
    "Kyrgyzstan": ("KG", "ky"),
    "Laos": ("LA", "lo"),
    "Latvia": ("LV", "lv"),
    "Lebanon": ("LB", "ar"),
    "Liberia": ("LR", "en"),
    "Libya": ("LY", "ar"),
    "Liechtenstein": ("LI", "de"),
    "Lithuania": ("LT", "lt"),
    "Luxembourg": ("LU", "lb"),
    "Macau": ("MO", "zh-MO"),
    "Malaysia": ("MY", "ms"),
    "Mali": ("ML", "fr"),
    "Maldives": ("MV", "dv"),
    "Malta": ("MT", "mt"),
    "Mauritius": ("MU", "en"),
    "Moldova": ("MD", "ro"),
    "Monaco": ("MC", "fr"),
    "Mongolia": ("MN", "mn"),
    "Morocco": ("MA", "ar"),
    "Mozambique": ("MZ", "pt"),
    "Myanmar": ("MM", "my"),
    "Namibia": ("NA", "en"),
    "Nepal": ("NP", "ne"),
    "New Zealand": ("NZ", "en-NZ"),
    "Nicaragua": ("NI", "es"),
    "Niger": ("NE", "fr"),
    "Nigeria": ("NG", "en"),
    "North Macedonia": ("MK", "mk"),
    "Norway": ("NO", "no"),
    "Oman": ("OM", "ar"),
    "Pakistan": ("PK", "ur"),
    "Panama": ("PA", "es"),
    "Papua New Guinea": ("PG", "en"),
    "Paraguay": ("PY", "es"),
    "Peru": ("PE", "es"),
    "Philippines": ("PH", "en"),
    "Rwanda": ("RW", "rw"),
    "Saudi Arabia": ("SA", "ar"),
    "Senegal": ("SN", "fr"),
    "Serbia": ("RS", "sr"),
    "Seychelles": ("SC", "en"),
    "Sierra Leone": ("SL", "en"),
    "Singapore": ("SG", "en"),
    "Slovakia": ("SK", "sk"),
    "Slovenia": ("SI", "sl"),
    "Solomon Islands": ("SB", "en"),
    "Somalia": ("SO", "so"),
    "Sri Lanka": ("LK", "si"),
    "Sudan": ("SD", "ar"),
    "Suriname": ("SR", "nl"),
    "Switzerland": ("CH", "de"),
    "Tajikistan": ("TJ", "tg"),
    "Tanzania": ("TZ", "sw"),
    "Togo": ("TG", "fr"),
    "Tonga": ("TO", "en"),
    "Trinidad and Tobago": ("TT", "en"),
    "Tunisia": ("TN", "ar"),
    "Turkmenistan": ("TM", "tk"),
    "Turks and Caicos Islands": ("TC", "en"),
    "Uganda": ("UG", "en"),
    "United Arab Emirates": ("AE", "ar"),
    "Uzbekistan": ("UZ", "uz"),
    "Uruguay": ("UY", "es"),
    "Vatican City": ("VA", "it"),
    "Venezuela": ("VE", "es"),
    "Yemen": ("YE", "ar"),
    "Zambia": ("ZM", "en"),
    "Zimbabwe": ("ZW", "en"),
}

# ---------------- GEO INSTALL AVAILABILITY LIST (EN) ----------------
# Used ONLY for /availability page. English names required.
# ---------------- GEO INSTALL AVAILABILITY LIST (EN) ----------------
# Used ONLY for /availability page. English names required.
COUNTRIES_GEO_EN: list[tuple[str, str]] = [
    ("Albania", "AL"),
    ("Algeria", "DZ"),
    ("Angola", "AO"),
    ("Antigua and Barbuda", "AG"),
    ("Argentina", "AR"),
    ("Armenia", "AM"),
    ("Aruba", "AW"),
    ("Australia", "AU"),
    ("Austria", "AT"),
    ("Azerbaijan", "AZ"),
    ("Bahamas", "BS"),
    ("Bahrain", "BH"),
    ("Bangladesh", "BD"),
    ("Belarus", "BY"),
    ("Belgium", "BE"),
    ("Belize", "BZ"),
    ("Benin", "BJ"),
    ("Bermuda", "BM"),
    ("Bolivia", "BO"),
    ("Bosnia and Herzegovina", "BA"),
    ("Botswana", "BW"),
    ("Brazil", "BR"),
    ("British Virgin Islands", "VG"),
    ("Bulgaria", "BG"),
    ("Burkina Faso", "BF"),
    ("Cambodia", "KH"),
    ("Cameroon", "CM"),
    ("Canada", "CA"),
    ("Cape Verde", "CV"),
    ("Cayman Islands", "KY"),
    ("Chad", "TD"),
    ("Chile", "CL"),
    ("China", "CN"),
    ("Colombia", "CO"),
    ("Comoros", "KM"),
    ("Congo - Brazzaville", "CG"),
    ("Congo - Kinshasa", "CD"),
    ("Costa Rica", "CR"),
    ("Croatia", "HR"),
    ("Cuba", "CU"),
    ("Cyprus", "CY"),
    ("Czechia", "CZ"),
    ("Côte d’Ivoire", "CI"),
    ("Denmark", "DK"),
    ("Djibouti", "DJ"),
    ("Dominica", "DM"),
    ("Dominican Republic", "DO"),
    ("Ecuador", "EC"),
    ("Egypt", "EG"),
    ("El Salvador", "SV"),
    ("Eritrea", "ER"),
    ("Estonia", "EE"),
    ("Fiji", "FJ"),
    ("Finland", "FI"),
    ("France", "FR"),
    ("Gabon", "GA"),
    ("Gambia", "GM"),
    ("Georgia", "GE"),
    ("Germany", "DE"),
    ("Ghana", "GH"),
    ("Gibraltar", "GI"),
    ("Greece", "GR"),
    ("Grenada", "GD"),
    ("Guatemala", "GT"),
    ("Guinea", "GN"),
    ("Guinea-Bissau", "GW"),
    ("Haiti", "HT"),
    ("Honduras", "HN"),
    ("Hong Kong", "HK"),
    ("Hungary", "HU"),
    ("Iceland", "IS"),
    ("India", "IN"),
    ("Indonesia", "ID"),
    ("Iran", "IR"),
    ("Iraq", "IQ"),
    ("Ireland", "IE"),
    ("Israel", "IL"),
    ("Italy", "IT"),
    ("Jamaica", "JM"),
    ("Japan", "JP"),
    ("Jordan", "JO"),
    ("Kazakhstan", "KZ"),
    ("Kenya", "KE"),
    ("Kuwait", "KW"),
    ("Kyrgyzstan", "KG"),
    ("Laos", "LA"),
    ("Latvia", "LV"),
    ("Lebanon", "LB"),
    ("Liberia", "LR"),
    ("Libya", "LY"),
    ("Liechtenstein", "LI"),
    ("Lithuania", "LT"),
    ("Luxembourg", "LU"),
    ("Macao", "MO"),
    ("Malaysia", "MY"),
    ("Maldives", "MV"),
    ("Mali", "ML"),
    ("Malta", "MT"),
    ("Mauritius", "MU"),
    ("Mexico", "MX"),
    ("Micronesia", "FM"),
    ("Moldova", "MD"),
    ("Monaco", "MC"),
    ("Mongolia", "MN"),
    ("Morocco", "MA"),
    ("Mozambique", "MZ"),
    ("Myanmar", "MM"),
    ("Namibia", "NA"),
    ("Nepal", "NP"),
    ("Netherlands", "NL"),
    ("New Zealand", "NZ"),
    ("Nicaragua", "NI"),
    ("Niger", "NE"),
    ("Nigeria", "NG"),
    ("North Macedonia", "MK"),
    ("Norway", "NO"),
    ("Oman", "OM"),
    ("Pakistan", "PK"),
    ("Panama", "PA"),
    ("Papua New Guinea", "PG"),
    ("Paraguay", "PY"),
    ("Peru", "PE"),
    ("Philippines", "PH"),
    ("Poland", "PL"),
    ("Portugal", "PT"),
    ("Qatar", "QA"),
    ("Romania", "RO"),
    ("Russia", "RU"),
    ("Rwanda", "RW"),
    ("Samoa", "WS"),
    ("San Marino", "SM"),
    ("Saudi Arabia", "SA"),
    ("Senegal", "SN"),
    ("Serbia", "RS"),
    ("Seychelles", "SC"),
    ("Sierra Leone", "SL"),
    ("Singapore", "SG"),
    ("Slovakia", "SK"),
    ("Slovenia", "SI"),
    ("Solomon Islands", "SB"),
    ("Somalia", "SO"),
    ("South Africa", "ZA"),
    ("South Korea", "KR"),
    ("Spain", "ES"),
    ("Sri Lanka", "LK"),
    ("St Kitts and Nevis", "KN"),
    ("St Lucia", "LC"),
    ("Sudan", "SD"),
    ("Suriname", "SR"),
    ("Sweden", "SE"),
    ("Switzerland", "CH"),
    ("Taiwan", "TW"),
    ("Tajikistan", "TJ"),
    ("Tanzania", "TZ"),
    ("Thailand", "TH"),
    ("Togo", "TG"),
    ("Tonga", "TO"),
    ("Trinidad and Tobago", "TT"),
    ("Tunisia", "TN"),
    ("Turkmenistan", "TM"),
    ("Turks and Caicos Islands", "TC"),
    ("Türkiye", "TR"),
    ("Uganda", "UG"),
    ("Ukraine", "UA"),
    ("United Arab Emirates", "AE"),
    ("United Kingdom", "GB"),
    ("United States", "US"),
    ("Uruguay", "UY"),
    ("Uzbekistan", "UZ"),
    ("Vanuatu", "VU"),
    ("Vatican City", "VA"),
    ("Venezuela", "VE"),
    ("Vietnam", "VN"),
    ("Yemen", "YE"),
    ("Zambia", "ZM"),
    ("Zimbabwe", "ZW"),
]

def get_geo_countries_en() -> list[tuple[str, str]]:
    return COUNTRIES_GEO_EN


def get_countries_by_mode(mode: str):
    return COUNTRIES_TOOLBOX if mode == "toolbox" else COUNTRIES_FULL


def get_country_meta_by_iso2(iso2: str) -> tuple[str, str]:
    code = (iso2 or "").strip().upper()
    for name, (cc, hl) in COUNTRIES_FULL.items():
        if cc.upper() == code:
            return name, hl
    for name, cc in COUNTRIES_GEO_EN:
        if cc.upper() == code:
            return name, "en"
    return code, "en"


def slugify_for_appmagic(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or "app"


def build_appmagic_app_url(app_id: str, app_name: str | None = None) -> str:
    return f"https://appmagic.rocks/google-play/{slugify_for_appmagic(app_name or app_id)}/{app_id}?hl=en"


def _load_appmagic_cached_auth() -> dict | None:
    try:
        with open(APPMAGIC_AUTH_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _save_appmagic_cached_auth(auth_key: str, email: str | None = None, user: dict | None = None):
    if not auth_key:
        return
    payload = {
        "auth_key": auth_key,
        "email": email,
        "user_id": user.get("id") if isinstance(user, dict) else None,
        "username": user.get("username") if isinstance(user, dict) else None,
        "saved_at": int(time.time()),
    }
    try:
        with open(APPMAGIC_AUTH_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        pass


def _clear_appmagic_cached_auth():
    try:
        if os.path.exists(APPMAGIC_AUTH_FILE):
            os.remove(APPMAGIC_AUTH_FILE)
    except Exception:
        pass


def normalize_appmagic_token(raw_token: str) -> str:
    token = str(raw_token or "").strip()
    if not token:
        return ""

    token = token.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").strip()

    if token.startswith("{"):
        try:
            data = json.loads(token)
            if isinstance(data, dict):
                for key in ("auth_key", "token", "access_token", "authorization", "Authorization"):
                    value = data.get(key)
                    if isinstance(value, str) and value.strip():
                        token = value.strip()
                        break
        except Exception:
            pass

    if token.lower().startswith("authorization:"):
        token = token.split(":", 1)[1].strip()
    elif token.lower().startswith("authorization "):
        token = token.split(None, 1)[1].strip()

    if token.lower().startswith("bearer "):
        token = token.split(None, 1)[1].strip()

    token = token.strip().strip("\"'`;,)}]")

    matches = [match.strip().strip("\"'`;,)}]") for match in APPMAGIC_TOKEN_PATTERN.findall(token)]
    if matches:
        preferred = next((match for match in matches if match.lower().startswith("u3rm")), None)
        token = preferred or next(
            (
                match
                for match in matches
                if match.lower() != "dashly_data/auth_token"
                and not looks_like_dashly_token(match)
            ),
            "",
        )

    # Header values in requests must be latin-1 encodable. Anything outside the
    # token alphabet is user-facing copy or a pasted placeholder, not auth data.
    if not re.fullmatch(r"[A-Za-z0-9._~+/\-=]{20,}", token or ""):
        return ""

    return token


def looks_like_dashly_token(token: str) -> bool:
    # Dashly is the support/chat widget used by App Magic. Its auth token is not
    # accepted by App Magic API endpoints such as data-countries.
    return bool(re.match(r"^user\.\d+\.7830-[a-f0-9]{16,}\.[a-f0-9]{16,}$", token or "", re.I))


def _browser_profile_roots() -> list[tuple[str, Path]]:
    home = Path.home()
    roots: list[tuple[str, Path]] = []

    if sys.platform == "darwin":
        roots.extend([
            ("Chrome", home / "Library/Application Support/Google/Chrome"),
            ("Chromium", home / "Library/Application Support/Chromium"),
            ("Brave", home / "Library/Application Support/BraveSoftware/Brave-Browser"),
            ("Edge", home / "Library/Application Support/Microsoft Edge"),
            ("Arc", home / "Library/Application Support/Arc/User Data"),
        ])
    elif sys.platform.startswith("win"):
        local = Path(os.environ.get("LOCALAPPDATA") or "")
        roots.extend([
            ("Chrome", local / "Google/Chrome/User Data"),
            ("Brave", local / "BraveSoftware/Brave-Browser/User Data"),
            ("Edge", local / "Microsoft/Edge/User Data"),
        ])
    else:
        roots.extend([
            ("Chrome", home / ".config/google-chrome"),
            ("Chromium", home / ".config/chromium"),
            ("Brave", home / ".config/BraveSoftware/Brave-Browser"),
            ("Edge", home / ".config/microsoft-edge"),
        ])

    return [(name, root) for name, root in roots if root.exists()]


def _candidate_browser_profiles(root: Path) -> list[Path]:
    profiles: list[Path] = []
    if (root / "Local Storage").exists() or (root / "IndexedDB").exists():
        profiles.append(root)

    try:
        children = list(root.iterdir())
    except Exception:
        return profiles

    for child in children:
        if not child.is_dir():
            continue
        if (
            child.name == "Default"
            or child.name.startswith("Profile ")
            or child.name in {"Guest Profile", "Person 1"}
            or (child / "Preferences").exists()
        ):
            profiles.append(child)

    seen = set()
    unique = []
    for profile in profiles:
        key = str(profile)
        if key not in seen:
            seen.add(key)
            unique.append(profile)
    return unique


def _appmagic_storage_files(profile: Path) -> list[Path]:
    files: list[Path] = []
    storage_dirs = [
        profile / "IndexedDB/https_appmagic.rocks_0.indexeddb.leveldb",
        profile / "Local Storage/leveldb",
        profile / "Session Storage",
    ]
    allowed_suffixes = {".ldb", ".log", ".sst", ".sqlite", ".localstorage"}

    for storage_dir in storage_dirs:
        if not storage_dir.exists():
            continue
        try:
            for path in storage_dir.rglob("*"):
                if path.is_file() and (not path.suffix or path.suffix.lower() in allowed_suffixes):
                    files.append(path)
        except Exception:
            continue

    return files


def _extract_appmagic_tokens_from_text(text: str) -> list[str]:
    patterns = [
        re.compile(r"Authorization[^A-Za-z0-9]{1,24}(Bearer\s+[A-Za-z0-9._~+/\-=]{20,})", re.I),
        re.compile(r"\b(Bearer\s+[A-Za-z0-9._~+/\-=]{20,})\b", re.I),
        re.compile(r'"(?:auth_key|authKey|access_token|accessToken|api_token)"\s*:\s*"([^"]{20,})"', re.I),
        re.compile(r"(?:auth_key|authKey|access_token|accessToken|authorization)[^A-Za-z0-9]{1,32}([A-Za-z0-9._~+/\-=]{20,})", re.I),
        re.compile(r"\b(u3rm[A-Za-z0-9._~+/\-=]{16,})\b"),
    ]

    tokens: list[str] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            token = normalize_appmagic_token(match.group(1))
            if not token:
                continue
            token = token.rstrip(";,)}]\"'")
            if looks_like_dashly_token(token):
                continue
            if "appmagic.rocks" in token.lower() or "sentry-" in token.lower():
                continue
            if len(token) < 20 or len(token) > 600:
                continue
            tokens.append(token)
    return tokens


def find_appmagic_token_in_browser_storage() -> tuple[str | None, dict]:
    candidates: list[dict] = []
    scanned_files = 0

    for browser_name, root in _browser_profile_roots():
        for profile in _candidate_browser_profiles(root):
            for path in _appmagic_storage_files(profile):
                scanned_files += 1
                try:
                    data = path.read_bytes()
                except Exception:
                    continue
                if not data:
                    continue

                try:
                    text = data.decode("utf-8", errors="ignore")
                except Exception:
                    text = data.decode("latin-1", errors="ignore")

                if "appmagic" not in text.lower() and "u3rm" not in text:
                    continue

                for token in _extract_appmagic_tokens_from_text(text):
                    score = 10
                    lower_text = text.lower()
                    if token.lower().startswith("bearer "):
                        score += 50
                    if normalize_appmagic_token(token).startswith("u3rm"):
                        score += 50
                    if "data-countries" in lower_text:
                        score += 30
                    if "authorization" in lower_text:
                        score += 20
                    if "dashly" in lower_text:
                        score -= 10

                    candidates.append({
                        "token": token,
                        "score": score,
                        "browser": browser_name,
                        "profile": profile.name,
                        "path": str(path),
                    })

    if not candidates:
        return None, {"scanned_files": scanned_files}

    candidates.sort(key=lambda item: item["score"], reverse=True)
    best = candidates[0]
    meta = {
        "browser": best["browser"],
        "profile": best["profile"],
        "matches": len(candidates),
        "scanned_files": scanned_files,
    }
    return best["token"], meta


def get_appmagic_cached_token() -> str | None:
    data = _load_appmagic_cached_auth()
    raw_token = (data or {}).get("auth_key")
    token = normalize_appmagic_token(raw_token) if isinstance(raw_token, str) else ""
    if token and not looks_like_dashly_token(token):
        if token != (raw_token or "").strip():
            _save_appmagic_cached_auth(token, email=(data or {}).get("email"))
        return token
    if raw_token:
        _clear_appmagic_cached_auth()
    return None


def get_appmagic_env_token() -> str | None:
    token = os.environ.get("APPMAGIC_BEARER_TOKEN") or os.environ.get("APPMAGIC_TOKEN")
    if token:
        return normalize_appmagic_token(token) or None
    return None


def get_appmagic_auth_token(ignore_env: bool = False) -> str | None:
    try:
        request_token = getattr(g, "appmagic_token_override", None)
    except Exception:
        request_token = None

    if request_token and not looks_like_dashly_token(request_token):
        return request_token

    if not ignore_env:
        token = get_appmagic_env_token()
        if token:
            return token
    return get_appmagic_cached_token()


def get_appmagic_auth_source() -> str:
    try:
        if getattr(g, "appmagic_token_override", None):
            return "request_token"
    except Exception:
        pass

    if os.environ.get("APPMAGIC_COOKIE"):
        return "env_cookie"
    if get_appmagic_env_token():
        return "env_token"
    if get_appmagic_cached_token():
        return "saved_login"
    if os.environ.get("APPMAGIC_BEARER_TOKEN") or os.environ.get("APPMAGIC_TOKEN"):
        return "env_token_invalid"
    return "none"


def appmagic_auth_available(ignore_env: bool = False) -> bool:
    return bool(
        get_appmagic_auth_token(ignore_env=ignore_env)
        or (not ignore_env and os.environ.get("APPMAGIC_COOKIE"))
    )


def appmagic_allow_estimates() -> bool:
    return (os.environ.get("APPMAGIC_ALLOW_ESTIMATES") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }


def build_appmagic_headers(
    referer: str | None = None,
    json_body: bool = False,
    with_auth: bool = False,
    ignore_env_auth: bool = False,
) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Origin": "https://appmagic.rocks",
        "Referer": referer or "https://appmagic.rocks/",
    }
    if json_body:
        headers["Content-Type"] = "application/json"

    if with_auth:
        token = get_appmagic_auth_token(ignore_env=ignore_env_auth)
        cookie = None if ignore_env_auth else os.environ.get("APPMAGIC_COOKIE")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if cookie:
            try:
                cookie.encode("latin-1")
                headers["Cookie"] = cookie
            except UnicodeEncodeError:
                pass

    return headers


def to_number(value):
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def to_int_or_none(value):
    number = to_number(value)
    if number is None or number <= 0:
        return None
    return int(round(number))


def appmagic_is_temporary_error(error: str | None) -> bool:
    if not error:
        return False
    if error.startswith("APPMAGIC_SEARCH_ERROR:"):
        return True
    if error.startswith("APPMAGIC_INFO_ERROR:"):
        return True
    if "_HTTP_" in error:
        status_text = error.rsplit("_HTTP_", 1)[-1].split(":", 1)[0]
        try:
            return int(status_text) in APPMAGIC_TEMPORARY_STATUS_CODES
        except Exception:
            return False
    return False


def appmagic_request_with_retry(method: str, url: str, **kwargs):
    last_response = None
    last_error = None
    for attempt in range(APPMAGIC_RETRIES + 1):
        try:
            response = session.request(method, url, **kwargs)
        except Exception as exc:
            last_error = exc
            if attempt >= APPMAGIC_RETRIES:
                raise
        else:
            last_response = response
            if response.status_code not in APPMAGIC_TEMPORARY_STATUS_CODES:
                return response
            if attempt >= APPMAGIC_RETRIES:
                return response

        time.sleep(APPMAGIC_RETRY_BASE_DELAY * (attempt + 1))

    if last_response is not None:
        return last_response
    if last_error is not None:
        raise last_error
    raise RuntimeError("APPMAGIC_RETRY_FAILED")


def parse_appmagic_login_link(raw_url: str) -> tuple[dict | None, str | None]:
    raw = (raw_url or "").strip()
    if not raw:
        return None, "APPMAGIC_LOGIN_LINK_EMPTY"

    if "://" not in raw and "code=" in raw:
        raw = "https://appmagic.rocks/login?" + raw.lstrip("?")

    q = parse_qs(urlparse(raw).query)
    code = (q.get("code", [""])[0] or "").strip()
    email = (q.get("email", [""])[0] or "").strip()

    if not code or not email:
        return None, "APPMAGIC_LOGIN_LINK_MISSING_CODE_OR_EMAIL"

    return {"code": code, "email": email}, None


def exchange_appmagic_login_link(raw_url: str) -> tuple[dict | None, str | None]:
    payload, error = parse_appmagic_login_link(raw_url)
    if error:
        return None, error

    auth_payload = {
        "code": payload["code"],
        "email": payload["email"],
        "lang": "en",
    }
    headers = build_appmagic_headers("https://appmagic.rocks/login?hl=en", json_body=True)

    try:
        r = appmagic_request_with_retry(
            "POST",
            APPMAGIC_EMAIL_AUTH_URL,
            json=auth_payload,
            headers=headers,
            timeout=APPMAGIC_TIMEOUT,
        )
    except Exception as e:
        return None, f"APPMAGIC_EMAIL_AUTH_ERROR:{e}"

    if r.status_code != 200:
        return None, f"APPMAGIC_EMAIL_AUTH_HTTP_{r.status_code}"

    try:
        data = r.json()
    except Exception:
        return None, "APPMAGIC_EMAIL_AUTH_BAD_JSON"

    if isinstance(data, dict) and data.get("message"):
        return None, f"APPMAGIC_EMAIL_AUTH_{data.get('message')}"

    user = data.get("data") if isinstance(data.get("data"), dict) else data
    auth_key = user.get("auth_key") if isinstance(user, dict) else None
    if not auth_key:
        return None, "APPMAGIC_EMAIL_AUTH_MISSING_AUTH_KEY"

    _save_appmagic_cached_auth(auth_key, payload["email"], user)
    return {
        "email": payload["email"],
        "source": "saved_login",
        "username": user.get("username"),
    }, None


# ---------------- BUNDLE RESOURCE PATH (PyInstaller-friendly) ----------------

def resource_path(rel_path: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base, rel_path)


# ----------------------------------------

app = Flask(
    __name__,
    template_folder=resource_path("templates"),
    static_folder=resource_path("static"),
)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 60 * 60 * 24 * 30
app.config["SECRET_KEY"] = (
    os.environ.get("SECRET_KEY")
    or os.environ.get("AUTH_SECRET_KEY")
    or "wwa-aso-tools-local-dev-secret-change-me"
)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
if (os.environ.get("SESSION_COOKIE_SECURE") or "").strip() == "1":
    app.config["SESSION_COOKIE_SECURE"] = True
session = requests.Session()
http_adapter = HTTPAdapter(pool_connections=HTTP_POOL_SIZE, pool_maxsize=HTTP_POOL_SIZE, max_retries=0)
session.mount("https://", http_adapter)
session.mount("http://", http_adapter)

_runtime_port = None
AUTH_DB_LOCK = threading.Lock()


def auth_db_path() -> str:
    path = os.path.expanduser(AUTH_DB_PATH)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path


def auth_db_connect():
    conn = sqlite3.connect(auth_db_path(), timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_auth_db():
    with AUTH_DB_LOCK:
        with auth_db_connect() as conn:
            conn.execute(
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")


def normalize_auth_email(email: str) -> str:
    return str(email or "").strip().lower()


def email_domain_allowed(email: str) -> bool:
    normalized = normalize_auth_email(email)
    return bool(normalized and normalized.endswith(AUTH_ALLOWED_EMAIL_DOMAIN))


def get_user_by_email(email: str):
    ensure_auth_db()
    with auth_db_connect() as conn:
        row = conn.execute(
            "SELECT id, email, password_hash, active, created_at, last_login_at FROM users WHERE email = ?",
            (normalize_auth_email(email),),
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id):
    if not user_id:
        return None
    ensure_auth_db()
    with auth_db_connect() as conn:
        row = conn.execute(
            "SELECT id, email, active, created_at, last_login_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def create_user(email: str, password: str):
    ensure_auth_db()
    normalized = normalize_auth_email(email)
    with AUTH_DB_LOCK:
        with auth_db_connect() as conn:
            conn.execute(
                "INSERT INTO users (email, password_hash, active, created_at) VALUES (?, ?, 1, ?)",
                (normalized, generate_password_hash(password), utc_now_iso()),
            )


def mark_user_login(user_id: int):
    with AUTH_DB_LOCK:
        with auth_db_connect() as conn:
            conn.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?",
                (utc_now_iso(), user_id),
            )


def login_user(user: dict):
    flask_session.clear()
    flask_session["user_id"] = user["id"]
    flask_session["user_email"] = user["email"]
    flask_session["session_nonce"] = secrets.token_urlsafe(12)
    mark_user_login(user["id"])


def logout_user():
    flask_session.clear()


def safe_next_url(value: str | None) -> str:
    next_url = str(value or "").strip()
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return url_for("index")


def request_wants_json() -> bool:
    if request.is_json:
        return True
    if request.path.startswith("/api/") or request.path.startswith("/tasks/"):
        return True
    return "application/json" in (request.headers.get("Accept") or "")


@app.context_processor
def inject_auth_context():
    return {
        "current_user_email": flask_session.get("user_email"),
        "auth_required": AUTH_REQUIRED,
        "auth_allowed_email_domain": AUTH_ALLOWED_EMAIL_DOMAIN,
    }


@app.before_request
def require_site_auth():
    g.current_user = None
    if not AUTH_REQUIRED:
        return None

    public_endpoints = {
        "health",
        "login",
        "register",
        "static",
        "task_check_availability",
    }
    if request.endpoint in public_endpoints:
        return None

    user = get_user_by_id(flask_session.get("user_id"))
    if user and int(user.get("active") or 0) == 1:
        g.current_user = user
        return None

    logout_user()
    if request_wants_json():
        return jsonify({"ok": False, "error": "AUTH_REQUIRED"}), 401
    return redirect(url_for("login", next=request.full_path if request.query_string else request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not AUTH_REQUIRED:
        return redirect(url_for("index"))

    next_url = safe_next_url(request.values.get("next"))
    error = ""
    email = normalize_auth_email(request.form.get("email") or "")

    if request.method == "POST":
        password = request.form.get("password") or ""
        user = get_user_by_email(email)
        if not user or int(user.get("active") or 0) != 1 or not check_password_hash(user["password_hash"], password):
            error = "Невірна пошта або пароль."
        else:
            login_user(user)
            return redirect(next_url)

    return render_template("auth.html", mode="login", email=email, error=error, next_url=next_url)


@app.route("/register", methods=["GET", "POST"])
def register():
    if not AUTH_REQUIRED:
        return redirect(url_for("index"))

    next_url = safe_next_url(request.values.get("next"))
    error = ""
    email = normalize_auth_email(request.form.get("email") or "")

    if request.method == "POST":
        password = request.form.get("password") or ""
        password_confirm = request.form.get("password_confirm") or ""

        if not email_domain_allowed(email):
            error = f"Реєстрація доступна тільки для пошти {AUTH_ALLOWED_EMAIL_DOMAIN}."
        elif len(password) < 8:
            error = "Пароль має містити мінімум 8 символів."
        elif password != password_confirm:
            error = "Паролі не співпадають."
        elif get_user_by_email(email):
            error = "Користувач з такою поштою вже існує."
        else:
            create_user(email, password)
            user = get_user_by_email(email)
            login_user(user)
            return redirect(next_url)

    return render_template("auth.html", mode="register", email=email, error=error, next_url=next_url)


@app.get("/logout")
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.get("/health")
def health():
    return "ok", 200


def is_local_request() -> bool:
    remote = (request.remote_addr or "").strip().lower()
    return remote in {"127.0.0.1", "::1", "localhost"} or remote.startswith("127.")


def local_exit_enabled() -> bool:
    return is_local_request() and (os.environ.get("WWA_ENABLE_LOCAL_EXIT") or "").strip() == "1"


@app.post("/shutdown")
def shutdown():
    if not local_exit_enabled():
        return jsonify({"error": "Forbidden"}), 403

    func = request.environ.get("werkzeug.server.shutdown")
    if func is None:
        os._exit(0)
    func()
    return "bye", 200


@app.post("/exit")
def exit_app():
    if not local_exit_enabled():
        return jsonify({"error": "Forbidden"}), 403

    try:
        _clear_state()
    except Exception:
        pass
    os._exit(0)


@app.get("/appmagic/auth/status")
def appmagic_auth_status():
    cached = _load_appmagic_cached_auth() or {}
    source = get_appmagic_auth_source()
    return jsonify({
        "authenticated": appmagic_auth_available(),
        "source": source,
        "email": cached.get("email") if source == "saved_login" else None,
        "is_local": is_local_request(),
        "hosted_env_configured": source in {"env_cookie", "env_token"},
        "can_auto_import": is_local_request(),
    })


@app.post("/appmagic/auth/exchange")
def appmagic_auth_exchange():
    if not is_local_request():
        return jsonify({
            "ok": False,
            "error": "APPMAGIC_LOGIN_LINK_LOCAL_ONLY",
        }), 400

    payload = request.json or {}
    raw_url = (
        payload.get("url")
        or payload.get("login_url")
        or payload.get("magic_link")
        or ""
    ).strip()

    auth, error = exchange_appmagic_login_link(raw_url)
    if error:
        status = 400 if error.startswith("APPMAGIC_LOGIN_LINK_") else 502
        return jsonify({"ok": False, "error": error}), status

    return jsonify({"ok": True, "auth": auth})


@app.post("/appmagic/auth/token")
def appmagic_auth_token():
    payload = request.json or {}
    raw_token = payload.get("token") or payload.get("auth_key") or ""
    token = normalize_appmagic_token(raw_token)
    email = (payload.get("email") or "").strip() or None

    if "dashly" in str(raw_token).lower() or looks_like_dashly_token(str(raw_token)):
        return jsonify({"ok": False, "error": "APPMAGIC_DASHLY_TOKEN_UNSUPPORTED"}), 400

    if not token:
        return jsonify({"ok": False, "error": "APPMAGIC_TOKEN_EMPTY"}), 400

    if looks_like_dashly_token(token):
        return jsonify({"ok": False, "error": "APPMAGIC_DASHLY_TOKEN_UNSUPPORTED"}), 400

    if is_local_request():
        _save_appmagic_cached_auth(token, email=email)

    return jsonify({
        "ok": True,
        "auth": {
            "source": "saved_login" if is_local_request() else "browser_session",
            "email": email,
        },
    })


@app.post("/appmagic/auth/auto-import")
def appmagic_auth_auto_import():
    if not is_local_request():
        return jsonify({
            "ok": False,
            "error": "APPMAGIC_AUTO_IMPORT_LOCAL_ONLY",
        }), 400

    token, meta = find_appmagic_token_in_browser_storage()

    if not token:
        return jsonify({
            "ok": False,
            "error": "APPMAGIC_BROWSER_TOKEN_NOT_FOUND",
            "scan": meta,
        }), 404

    if looks_like_dashly_token(token):
        return jsonify({
            "ok": False,
            "error": "APPMAGIC_DASHLY_TOKEN_UNSUPPORTED",
            "scan": meta,
        }), 400

    _save_appmagic_cached_auth(token)
    return jsonify({
        "ok": True,
        "auth": {
            "source": "saved_login",
            "method": "browser_storage",
            "browser": meta.get("browser"),
            "profile": meta.get("profile"),
            "matches": meta.get("matches"),
        },
    })


@app.post("/appmagic/auth/logout")
def appmagic_auth_logout():
    _clear_appmagic_cached_auth()
    return jsonify({"ok": True})


# ---------------- SINGLE INSTANCE HELPERS ----------------

def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _ping_local_server(port: int) -> bool:
    try:
        r = requests.get(f"http://{HOST}:{port}/health", timeout=0.5)
        return r.status_code == 200
    except Exception:
        return False


def _load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_state(pid: int, port: int):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"pid": pid, "port": port}, f)
    except Exception:
        pass


def _clear_state():
    try:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
    except Exception:
        pass


def _pick_free_port() -> int:
    s = socket.socket()
    s.bind((HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


def ensure_single_instance_or_get_port(preferred_port: int) -> int:
    st = _load_state()
    if isinstance(st, dict):
        pid = int(st.get("pid") or 0)
        port = int(st.get("port") or 0)

        if pid > 0 and port > 0 and _is_process_alive(pid) and _ping_local_server(port):
            webbrowser.open(f"http://{HOST}:{port}/")
            raise SystemExit(0)

    try:
        with socket.socket() as s:
            s.bind((HOST, preferred_port))
        return preferred_port
    except Exception:
        return _pick_free_port()


# ---------------- URL HELPERS ----------------

def detect_store(url: str) -> str:
    u = (url or "").lower()
    if "play.google.com/store/apps" in u:
        return "google_play"
    if "appmagic.rocks/google-play/" in u:
        return "appmagic_google_play"
    if "apps.apple.com" in u:
        return "apple_app_store"
    return "unknown"


def extract_google_play_app_id(play_url: str) -> str:
    q = parse_qs(urlparse(play_url).query)
    raw = (q.get("id", [""])[0] or "").strip()
    raw = re.sub(r"[^a-zA-Z0-9._]", "", raw)
    return raw


def extract_appmagic_google_play_app_id(appmagic_url: str) -> str:
    parts = [part for part in urlparse(appmagic_url).path.split("/") if part]
    if len(parts) >= 3 and parts[0] == "google-play":
        raw = parts[2].strip()
        return re.sub(r"[^a-zA-Z0-9._]", "", raw)
    return ""


def build_google_play_url(app_id: str, gl: str, hl: str) -> str:
    return f"https://play.google.com/store/apps/details?id={app_id}&gl={gl}&hl={hl}"


def extract_apple_app_id(apple_url: str) -> str:
    m = re.search(r"id(\d+)", apple_url)
    return m.group(1) if m else ""


def extract_apple_lang_param(apple_url: str) -> str | None:
    q = parse_qs(urlparse(apple_url).query)
    return q.get("l", [None])[0]


def build_apple_store_url(app_id: str, country_iso2: str, l: str | None) -> str:
    cc = country_iso2.lower()
    if l:
        return f"https://apps.apple.com/{cc}/app/id{app_id}?l={l}"
    return f"https://apps.apple.com/{cc}/app/id{app_id}"


# ---------------- FETCHERS (RATINGS) ----------------

def fetch_google_play_rating(app_id: str, gl: str, hl: str):
    cache_key = ("google_rating", app_id.lower(), gl.upper(), hl)
    cached = GOOGLE_RATING_CACHE.get(cache_key)
    if cached is not CACHE_MISS:
        return cached

    time.sleep(random.uniform(GOOGLE_JITTER_MIN, GOOGLE_JITTER_MAX))

    url = build_google_play_url(app_id, gl, hl)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": hl,
    }

    try:
        r = session.get(url, headers=headers, timeout=30)
    except Exception as e:
        return None, str(e)

    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"

    low = (r.text or "").lower()
    if "consent.google.com" in low or "unusual traffic" in low:
        return None, "CONSENT/BLOCKED"

    soup = BeautifulSoup(r.text, "lxml")
    for s in soup.find_all("script", {"type": "application/ld+json"}):
        raw = (s.string or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue

        items = data if isinstance(data, list) else [data]
        for obj in items:
            if not isinstance(obj, dict):
                continue
            ar = obj.get("aggregateRating")
            if isinstance(ar, dict) and "ratingValue" in ar:
                try:
                    result = (float(str(ar["ratingValue"]).replace(",", ".")), None)
                    GOOGLE_RATING_CACHE.set(cache_key, result)
                    return result
                except Exception:
                    pass

    result = (None, "RATING_NOT_FOUND")
    GOOGLE_RATING_CACHE.set(cache_key, result)
    return result


def format_exact_downloads_label(total_downloads) -> str:
    total = to_int_or_none(total_downloads)
    if not total:
        return "—"
    return f"≈ {total:,}".replace(",", " ")


def fetch_google_play_install_range(app_id: str) -> dict | None:
    cache_key = ("google_install_range", app_id.lower())
    cached = GOOGLE_INSTALL_RANGE_CACHE.get(cache_key)
    if cached is not CACHE_MISS:
        return cached

    url = build_google_play_url(
        app_id,
        GOOGLE_PLAY_DEFAULT_INSTALL_COUNTRY,
        GOOGLE_PLAY_DEFAULT_INSTALL_LANG,
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        r = session.get(url, headers=headers, timeout=30)
    except Exception:
        return None

    if r.status_code != 200:
        return None

    text = r.text or ""
    if "consent.google.com" in text.lower() or "unusual traffic" in text.lower():
        return None

    # Google Play embeds the public install bucket as e.g.
    # ["1,000+",1000,2326,"1K+"]. The second value is the lower bound.
    matches = []
    pattern = re.compile(
        r'\["((?:[0-9][0-9,\.\s]*|[0-9]+(?:\.\d+)?[KMB])\+)",(\d{1,12}),\d+,"([^"]*\+)"\]'
    )
    for match in pattern.finditer(text):
        lower_bound = to_int_or_none(match.group(2))
        if not lower_bound:
            continue
        matches.append({
            "label": match.group(1),
            "short_label": match.group(3),
            "min_installs": lower_bound,
        })

    result = matches[0] if matches else None
    GOOGLE_INSTALL_RANGE_CACHE.set(cache_key, result)
    return result


def fetch_apple_store_rating(app_id: str, country_iso2: str):
    cache_key = ("apple_rating", str(app_id), country_iso2.upper())
    cached = APPLE_RATING_CACHE.get(cache_key)
    if cached is not CACHE_MISS:
        return cached

    cc = country_iso2.lower()
    url = "https://itunes.apple.com/lookup"
    params = {"id": app_id, "country": cc}
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

    try:
        r = session.get(url, params=params, headers=headers, timeout=30)
    except Exception as e:
        return None, str(e)

    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"

    try:
        data = r.json()
    except Exception:
        return None, "BAD_JSON"

    results = data.get("results") or []
    if not results:
        result = (None, "NOT_AVAILABLE")
        APPLE_RATING_CACHE.set(cache_key, result)
        return result

    obj = results[0]
    for key in ("averageUserRating", "averageUserRatingForCurrentVersion", "userRatingValue"):
        val = obj.get(key)
        if val is None:
            continue
        try:
            val = float(val)
        except Exception:
            continue
        if val <= 0:
            result = (None, "NO_RATINGS_YET")
            APPLE_RATING_CACHE.set(cache_key, result)
            return result
        result = (val, None)
        APPLE_RATING_CACHE.set(cache_key, result)
        return result

    result = (None, "NO_RATINGS_YET")
    APPLE_RATING_CACHE.set(cache_key, result)
    return result


# ---------------- FETCHERS (APP MAGIC) ----------------

def fetch_appmagic_united_app(app_id: str):
    cache_key = ("appmagic_search", app_id.lower())
    cached = APPMAGIC_SEARCH_CACHE.get(cache_key)
    if cached is not CACHE_MISS:
        return cached

    payload = {"ids": [{"store": 1, "store_application_id": app_id}]}
    headers = build_appmagic_headers(build_appmagic_app_url(app_id), json_body=True)

    try:
        r = appmagic_request_with_retry(
            "POST",
            APPMAGIC_SEARCH_BY_IDS_URL,
            json=payload,
            headers=headers,
            timeout=APPMAGIC_TIMEOUT,
        )
    except Exception as e:
        return None, f"APPMAGIC_SEARCH_ERROR:{e}"

    if r.status_code != 200:
        return None, f"APPMAGIC_SEARCH_HTTP_{r.status_code}"

    try:
        data = r.json()
    except Exception:
        return None, "APPMAGIC_SEARCH_BAD_JSON"

    if data.get("message"):
        return None, f"APPMAGIC_SEARCH_{data.get('message')}"

    apps = data.get("data") or []
    store_id = f"1_{app_id}"
    for item in apps:
        if store_id in (item.get("store_ids") or []):
            result = (item, None)
            APPMAGIC_SEARCH_CACHE.set(cache_key, result)
            return result

    return None, "APPMAGIC_APP_NOT_FOUND"


def fetch_appmagic_app_info(app_id: str, country_iso2: str):
    iso2 = (country_iso2 or "").upper()
    cache_key = ("appmagic_info", app_id.lower(), iso2)
    cached = APPMAGIC_INFO_CACHE.get(cache_key)
    if cached is not CACHE_MISS:
        return cached

    payload = {"store": 1, "storeApplicationID": app_id, "country": iso2}
    headers = build_appmagic_headers(build_appmagic_app_url(app_id), json_body=True)

    try:
        r = appmagic_request_with_retry(
            "POST",
            APPMAGIC_APP_INFO_URL,
            json=payload,
            headers=headers,
            timeout=APPMAGIC_TIMEOUT,
        )
    except Exception as e:
        return None, f"APPMAGIC_INFO_ERROR:{e}"

    if r.status_code != 200:
        return None, f"APPMAGIC_INFO_HTTP_{r.status_code}"

    try:
        data = r.json()
    except Exception:
        return None, "APPMAGIC_INFO_BAD_JSON"

    if not isinstance(data, dict):
        return None, "APPMAGIC_INFO_BAD_JSON"

    if data.get("message"):
        return None, f"APPMAGIC_INFO_{data.get('message')}"

    result = (data.get("data"), None)
    APPMAGIC_INFO_CACHE.set(cache_key, result)
    return result


def fetch_appmagic_data_countries(united_application_id, app_id: str, app_name: str):
    if not appmagic_auth_available():
        return None, "APPMAGIC_DATA_COUNTRIES_AUTH_REQUIRED"

    cache_key = ("appmagic_data_countries", str(united_application_id), app_id.lower())
    cached = APPMAGIC_DATA_COUNTRIES_CACHE.get(cache_key)
    if cached is not CACHE_MISS:
        return cached

    params = {"united_application_id": united_application_id}
    referer = build_appmagic_app_url(app_id, app_name)

    def request_data_countries(ignore_env_auth: bool = False):
        headers = build_appmagic_headers(
            referer,
            with_auth=True,
            ignore_env_auth=ignore_env_auth,
        )
        return appmagic_request_with_retry(
            "GET",
            APPMAGIC_DATA_COUNTRIES_URL,
            params=params,
            headers=headers,
            timeout=APPMAGIC_TIMEOUT,
        )

    try:
        r = request_data_countries(ignore_env_auth=False)
    except Exception as e:
        return None, f"APPMAGIC_DATA_COUNTRIES_ERROR:{e}"

    env_auth_present = bool(
        os.environ.get("APPMAGIC_COOKIE")
        or os.environ.get("APPMAGIC_BEARER_TOKEN")
        or os.environ.get("APPMAGIC_TOKEN")
    )
    if (
        r.status_code in (401, 403)
        and env_auth_present
        and appmagic_auth_available(ignore_env=True)
    ):
        try:
            retry_response = request_data_countries(ignore_env_auth=True)
            if retry_response.status_code not in (401, 403):
                r = retry_response
        except Exception:
            pass

    if r.status_code in (401, 403):
        return None, "APPMAGIC_DATA_COUNTRIES_UNAUTHORIZED"
    if r.status_code != 200:
        return None, f"APPMAGIC_DATA_COUNTRIES_HTTP_{r.status_code}"

    try:
        data = r.json()
    except Exception:
        return None, "APPMAGIC_DATA_COUNTRIES_BAD_JSON"

    if isinstance(data, dict) and data.get("message"):
        return None, f"APPMAGIC_DATA_COUNTRIES_{data.get('message')}"

    rows = data.get("data") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return None, "APPMAGIC_DATA_COUNTRIES_BAD_DATA"

    result = (rows, None)
    APPMAGIC_DATA_COUNTRIES_CACHE.set(cache_key, result)
    return result


def get_appmagic_row_country(row: dict) -> str:
    for key in ("Country", "country", "countryCode", "country_code"):
        value = row.get(key)
        if isinstance(value, str):
            value = value.strip().upper()
            if re.fullmatch(r"[A-Z]{2}|WW", value):
                return value
    return ""


def build_appmagic_period_distribution(country_rows: list[dict], period: str) -> tuple[dict, list[dict]]:
    fields = APPMAGIC_PERIOD_FIELDS[period]
    total_key = fields["downloads"]
    percent_key = fields["percent"]

    ww_row = next((row for row in country_rows if get_appmagic_row_country(row) == "WW"), None)
    country_items = []
    for row in country_rows:
        iso2 = get_appmagic_row_country(row)
        if not re.fullmatch(r"[A-Z]{2}", iso2 or "") or iso2 == "WW":
            continue

        downloads = to_int_or_none(row.get(total_key))
        percent_value = to_number(row.get(percent_key))
        if downloads is None and (percent_value is None or percent_value <= 0):
            continue

        country_name, hl = get_country_meta_by_iso2(iso2)
        country_items.append({
            "country": country_name,
            "gl": iso2,
            "hl": hl,
            "downloads": downloads,
            "raw_percent": max(0.0, percent_value or 0.0),
        })

    country_items.sort(
        key=lambda item: (item["raw_percent"], item.get("downloads") or 0),
        reverse=True,
    )

    total_downloads = to_int_or_none(ww_row.get(total_key)) if isinstance(ww_row, dict) else None
    if total_downloads is None:
        total_downloads = sum(item.get("downloads") or 0 for item in country_items) or None

    if total_downloads and any(item["raw_percent"] <= 0 for item in country_items):
        for item in country_items:
            if item["raw_percent"] <= 0 and item.get("downloads"):
                item["raw_percent"] = (item["downloads"] / total_downloads) * 100

    visible = country_items

    if visible:
        raw_shares = [max(0.0, item.get("raw_percent") or 0.0) / 100 for item in visible]
        if sum(raw_shares) <= 0:
            weights = [item.get("downloads") or 0 for item in visible]
            total_weight = sum(weights) or 1
            raw_shares = [weight / total_weight for weight in weights]
        percents = round_percentages_to_100(raw_shares)
    else:
        percents = []

    distribution = []
    for idx, (item, share) in enumerate(zip(visible, percents), start=1):
        color = APPMAGIC_COLORS[(idx - 1) % len(APPMAGIC_COLORS)]
        distribution.append({
            "country": item["country"],
            "gl": item.get("gl"),
            "rank": idx,
            "share": share,
            "share_exact": round(max(0.0, item.get("raw_percent") or 0.0), 4),
            "estimated_installs": item.get("downloads"),
            "color": color,
            "is_other": False,
        })

    return {
        "label": fields["label"],
        "downloads_total": total_downloads,
        "downloads_label": format_appmagic_downloads_label(total_downloads),
        "downloads_distribution": distribution,
    }, country_items


def build_exact_appmagic_geos(app_id: str, app_name: str, country_rows: list[dict]) -> tuple[list[dict], dict]:
    periods = {}
    items_by_period = {}
    for period in APPMAGIC_PERIOD_FIELDS:
        period_meta, period_items = build_appmagic_period_distribution(country_rows, period)
        periods[period] = period_meta
        items_by_period[period] = period_items

    ranking_items = items_by_period["last30days"] or items_by_period["lifetime"]
    visible_items = [
        item for item in ranking_items
        if item.get("gl") and not item.get("is_other")
    ]

    row_by_iso_period = {
        period: {item["gl"]: item for item in items if item.get("gl")}
        for period, items in items_by_period.items()
    }

    geos = []
    for idx, item in enumerate(visible_items, start=1):
        iso2 = item["gl"]
        country_name, hl = get_country_meta_by_iso2(iso2)
        periods_for_geo = {}
        for period in APPMAGIC_PERIOD_FIELDS:
            period_item = row_by_iso_period.get(period, {}).get(iso2) or {}
            period_dist = next(
                (
                    dist_item for dist_item in periods[period]["downloads_distribution"]
                    if dist_item.get("gl") == iso2
                ),
                {},
            )
            periods_for_geo[period] = {
                "share": period_dist.get("share"),
                "estimated_installs": period_item.get("downloads"),
            }

        geos.append({
            "country": country_name,
            "gl": iso2,
            "hl": hl,
            "appmagic_rank": idx,
            "appmagic_downloads": periods_for_geo["last30days"].get("estimated_installs"),
            "appmagic_share": periods_for_geo["last30days"].get("share"),
            "appmagic_estimated_installs": periods_for_geo["last30days"].get("estimated_installs"),
            "appmagic_periods": periods_for_geo,
            "appmagic_error": None,
            "appmagic_url": build_appmagic_app_url(app_id, app_name),
            "appmagic_country_url": None,
        })

    return geos, {
        "downloads_total": periods["last30days"]["downloads_total"],
        "downloads_label": periods["last30days"]["downloads_label"],
        "downloads_distribution": periods["last30days"]["downloads_distribution"],
        "downloads_periods": periods,
        "downloads_estimate_source": "appmagic_data_countries",
        "downloads_period_active": "last30days",
        "appmagic_has_exact_country_split": True,
        "appmagic_top_geo_limit": len(visible_items),
        "appmagic_country_count": len(visible_items),
    }


def appmagic_period_counts_need_repair(period_meta: dict) -> bool:
    distribution = period_meta.get("downloads_distribution") or []
    country_items = [item for item in distribution if not item.get("is_other")]
    if not country_items:
        return False

    item_stats = []
    for item in country_items:
        installs = to_int_or_none(item.get("estimated_installs"))
        share = to_number(item.get("share_exact"))
        if share is None or share <= 0:
            share = to_number(item.get("share")) or 0
        item_stats.append({
            "installs": installs,
            "share": max(0.0, share),
        })

    install_values = [
        item["installs"]
        for item in item_stats
        if item["installs"] is not None
    ]
    if not install_values:
        return False

    max_share = max(item["share"] for item in item_stats)
    total_downloads = to_int_or_none(period_meta.get("downloads_total"))

    if max(install_values) <= APPMAGIC_PLACEHOLDER_INSTALL_MAX and max_share >= 5:
        return True

    if not total_downloads:
        return False

    for item in item_stats:
        installs = item["installs"]
        if installs is None or installs > APPMAGIC_PLACEHOLDER_INSTALL_MAX:
            continue
        expected_installs = total_downloads * (item["share"] / 100)
        if expected_installs >= APPMAGIC_REPAIR_MIN_EXPECTED_INSTALLS:
            return True

    return False


def get_appmagic_total_download_fallback(
    app_id: str,
    app_info_downloads: list[int | None] | None = None,
) -> tuple[int | None, str | None, str | None]:
    info_values = [
        value for value in (app_info_downloads or [])
        if isinstance(value, int) and value > 1
    ]
    if info_values:
        return max(info_values), "appmagic_share_app_info_total", "App Magic app-info total"

    gp_range = fetch_google_play_install_range(app_id)
    if gp_range and gp_range.get("min_installs"):
        return int(gp_range["min_installs"]), "appmagic_share_google_play_total", "Google Play public installs"

    return None, None, None


def allocate_appmagic_installs_by_share(distribution: list[dict], total_downloads: int) -> dict[str, int]:
    total = to_int_or_none(total_downloads)
    if not total:
        return {}

    items = []
    for dist_item in distribution:
        if dist_item.get("is_other"):
            continue
        iso2 = (dist_item.get("gl") or "").upper()
        if not iso2:
            continue
        share_exact = to_number(dist_item.get("share_exact"))
        if share_exact is None or share_exact <= 0:
            share_exact = to_number(dist_item.get("share")) or 0
        if share_exact <= 0:
            continue
        raw_value = total * (share_exact / 100)
        items.append({
            "iso2": iso2,
            "raw": raw_value,
            "fraction": raw_value - int(raw_value),
        })

    if not items:
        return {}

    values = []
    for item in items:
        floor_value = int(item["raw"])
        if item["raw"] > 0 and floor_value == 0 and total >= len(items):
            floor_value = 1
        values.append(floor_value)

    delta = total - sum(values)
    if delta > 0:
        order = sorted(range(len(items)), key=lambda idx: items[idx]["fraction"], reverse=True)
        idx = 0
        while delta > 0 and order:
            values[order[idx % len(order)]] += 1
            delta -= 1
            idx += 1
    elif delta < 0:
        order = sorted(
            range(len(items)),
            key=lambda idx: (values[idx] - items[idx]["raw"], values[idx]),
            reverse=True,
        )
        idx = 0
        while delta < 0 and order:
            target = order[idx % len(order)]
            if values[target] > 0:
                values[target] -= 1
                delta += 1
            idx += 1
            if idx > len(order) * (total + len(order) + 1):
                break

    return {
        item["iso2"]: max(0, int(value))
        for item, value in zip(items, values)
    }


def repair_appmagic_placeholder_installs(
    app_id: str,
    geos: list[dict],
    appmagic_meta: dict,
    app_info_downloads: list[int | None] | None = None,
):
    periods = appmagic_meta.get("downloads_periods") or {}
    broken_periods = [
        period for period, period_meta in periods.items()
        if appmagic_period_counts_need_repair(period_meta)
    ]
    if not broken_periods:
        return

    external_fallback_total = None
    external_source = None
    external_source_label = None

    geo_by_iso = {
        (geo.get("gl") or "").upper(): geo
        for geo in geos
        if geo.get("gl")
    }

    for period in broken_periods:
        period_meta = periods.get(period) or {}
        distribution = period_meta.get("downloads_distribution") or []
        country_count = len([item for item in distribution if not item.get("is_other")])
        period_total = to_int_or_none(period_meta.get("downloads_total"))
        if period_total and period_total > max(1, country_count):
            fallback_total = period_total
            source = "appmagic_data_countries_total_share"
            source_label = "App Magic data-countries total"
        else:
            if external_fallback_total is None:
                (
                    external_fallback_total,
                    external_source,
                    external_source_label,
                ) = get_appmagic_total_download_fallback(app_id, app_info_downloads)
            fallback_total = external_fallback_total
            source = external_source
            source_label = external_source_label

        if not fallback_total or not source:
            continue

        estimates_by_iso = allocate_appmagic_installs_by_share(distribution, fallback_total)

        for dist_item in distribution:
            if dist_item.get("is_other"):
                continue
            iso2 = (dist_item.get("gl") or "").upper()
            estimated_installs = estimates_by_iso.get(iso2)

            dist_item["estimated_installs"] = estimated_installs
            dist_item["estimated_installs_source"] = source

            geo = geo_by_iso.get(iso2)
            if not geo:
                continue
            geo.setdefault("appmagic_periods", {}).setdefault(period, {})
            geo["appmagic_periods"][period]["estimated_installs"] = estimated_installs
            geo["appmagic_periods"][period]["estimated_installs_source"] = source
            if period == "last30days":
                geo["appmagic_downloads"] = estimated_installs
                geo["appmagic_estimated_installs"] = estimated_installs

        period_meta["downloads_total"] = fallback_total
        if source == "appmagic_data_countries_total_share":
            period_meta["downloads_label"] = format_appmagic_downloads_label(fallback_total)
        else:
            period_meta["downloads_label"] = format_exact_downloads_label(fallback_total)
        period_meta["downloads_total_source"] = source
        period_meta["downloads_repair_source_label"] = source_label
        period_meta["downloads_country_sum"] = sum(
            item.get("estimated_installs") or 0
            for item in distribution
            if not item.get("is_other")
        )

    active_period = appmagic_meta.get("downloads_period_active") or "last30days"
    active_meta = periods.get(active_period) or {}
    appmagic_meta["downloads_total"] = active_meta.get("downloads_total")
    appmagic_meta["downloads_label"] = active_meta.get("downloads_label")
    appmagic_meta["downloads_distribution"] = active_meta.get("downloads_distribution") or []
    appmagic_meta["downloads_estimate_source"] = (
        active_meta.get("downloads_total_source")
        or appmagic_meta.get("downloads_estimate_source")
    )
    appmagic_meta["downloads_repaired_periods"] = [
        period
        for period in broken_periods
        if (periods.get(period) or {}).get("downloads_total_source")
    ]
    appmagic_meta["downloads_repair_source_label"] = active_meta.get("downloads_repair_source_label")


def build_appmagic_unavailable_fallback(app_id: str, reason: str):
    appmagic_url = build_appmagic_app_url(app_id)
    fallback_iso2 = APPMAGIC_FALLBACK_TOP_GEO_ISO2[:APPMAGIC_TOP_GEO_LIMIT]
    geos = []

    for idx, iso2 in enumerate(fallback_iso2, start=1):
        country_name, hl = get_country_meta_by_iso2(iso2)
        geos.append({
            "country": country_name,
            "gl": iso2,
            "hl": hl,
            "appmagic_rank": idx,
            "appmagic_downloads": None,
            "appmagic_share": None,
            "appmagic_estimated_installs": None,
            "appmagic_periods": None,
            "appmagic_error": f"App Magic unavailable ({reason}); fallback GEO set",
            "appmagic_url": appmagic_url,
            "appmagic_country_url": None,
        })

    geos, downloads_meta = enrich_appmagic_download_estimates(
        geos,
        all_country_infos=None,
        has_country_values=False,
        top_limit=APPMAGIC_TOP_GEO_LIMIT,
    )
    downloads_meta["downloads_estimate_source"] = "appmagic_unavailable_fallback"

    return geos, {
        **downloads_meta,
        "appmagic_app_id": None,
        "appmagic_app_name": app_id,
        "appmagic_url": appmagic_url,
        "raw_data_countries": fallback_iso2,
        "appmagic_all_country_infos": None,
        "appmagic_has_country_values": False,
        "appmagic_data_countries_error": reason,
        "appmagic_has_exact_country_split": False,
        "appmagic_top_geo_limit": APPMAGIC_TOP_GEO_LIMIT,
        "appmagic_unavailable": True,
        "appmagic_fallback_reason": reason,
    }, None


def build_appmagic_exact_required_meta(united_app: dict, app_id: str, app_name: str, appmagic_url: str, reason: str):
    empty_meta = _empty_appmagic_download_meta()
    empty_meta.update({
        "downloads_estimate_source": "appmagic_auth_required",
        "appmagic_app_id": united_app.get("id"),
        "appmagic_app_name": app_name,
        "appmagic_url": appmagic_url,
        "raw_data_countries": united_app.get("dataCountries") or [],
        "appmagic_all_country_infos": None,
        "appmagic_has_country_values": False,
        "appmagic_has_exact_country_split": False,
        "appmagic_auth_required": True,
        "appmagic_exact_required_reason": reason,
        "appmagic_auth_source": get_appmagic_auth_source(),
        "appmagic_data_countries_error": reason,
        "appmagic_top_geo_limit": APPMAGIC_TOP_GEO_LIMIT,
    })
    return empty_meta


def appmagic_exact_error_requires_auth(error: str | None) -> bool:
    return error in {
        "APPMAGIC_DATA_COUNTRIES_AUTH_REQUIRED",
        "APPMAGIC_DATA_COUNTRIES_UNAUTHORIZED",
    }


def build_appmagic_no_rating_data_meta(united_app: dict, app_id: str, app_name: str, appmagic_url: str, reason: str):
    empty_meta = _empty_appmagic_download_meta()
    empty_meta.update({
        "downloads_estimate_source": "appmagic_no_rating_data",
        "appmagic_app_id": united_app.get("id"),
        "appmagic_app_name": app_name,
        "appmagic_url": appmagic_url,
        "raw_data_countries": united_app.get("dataCountries") or [],
        "appmagic_all_country_infos": None,
        "appmagic_has_country_values": False,
        "appmagic_has_exact_country_split": False,
        "appmagic_auth_required": False,
        "appmagic_no_rating_data": True,
        "appmagic_no_rating_data_reason": reason,
        "appmagic_data_countries_error": reason,
        "appmagic_top_geo_limit": 0,
        "appmagic_country_count": 0,
    })
    return empty_meta


def fetch_appmagic_top_geos(app_id: str):
    united_app, error = fetch_appmagic_united_app(app_id)
    if error:
        if appmagic_is_temporary_error(error):
            return build_appmagic_unavailable_fallback(app_id, error)
        return [], {}, error

    app_name = united_app.get("name") or app_id
    appmagic_url = build_appmagic_app_url(app_id, app_name)
    exact_rows, exact_error = fetch_appmagic_data_countries(united_app.get("id"), app_id, app_name)
    if exact_rows:
        exact_geos, exact_meta = build_exact_appmagic_geos(app_id, app_name, exact_rows)
        if exact_geos:
            def enrich_country_url(geo: dict):
                info, info_error = fetch_appmagic_app_info(app_id, geo["gl"])
                if isinstance(info, dict):
                    appmagic_country_url = info.get("application_url")
                    geo["_appmagic_info_downloads"] = to_int_or_none(info.get("downloads"))
                    if appmagic_country_url:
                        geo["appmagic_country_url"] = appmagic_country_url
                        q = parse_qs(urlparse(appmagic_country_url).query)
                        geo["hl"] = q.get("hl", [geo["hl"]])[0] or geo["hl"]
                geo["appmagic_error"] = info_error
                return geo

            with ThreadPoolExecutor(max_workers=MAX_WORKERS_APPMAGIC) as ex:
                exact_geos = list(ex.map(enrich_country_url, exact_geos))

            app_info_downloads = [
                geo.get("_appmagic_info_downloads")
                for geo in exact_geos
            ]
            repair_appmagic_placeholder_installs(
                app_id,
                exact_geos,
                exact_meta,
                app_info_downloads,
            )
            exact_meta.update(build_app_total_installs_meta(
                app_id,
                app_info_downloads=app_info_downloads,
                data_countries_total=exact_meta.get("downloads_total"),
            ))
            for geo in exact_geos:
                geo.pop("_appmagic_info_downloads", None)

            exact_geos.sort(key=lambda r: (r["appmagic_rank"] or 9999, r["country"]))
            exact_meta.update({
                "appmagic_app_id": united_app.get("id"),
                "appmagic_app_name": app_name,
                "appmagic_url": appmagic_url,
                "raw_data_countries": united_app.get("dataCountries") or [],
                "appmagic_data_countries_error": None,
            })
            return exact_geos, exact_meta, None

    if not appmagic_allow_estimates():
        reason = exact_error or "APPMAGIC_EXACT_DATA_UNAVAILABLE"
        if appmagic_exact_error_requires_auth(reason):
            exact_required_meta = build_appmagic_exact_required_meta(
                united_app,
                app_id,
                app_name,
                appmagic_url,
                reason,
            )
            exact_required_meta.update(build_app_total_installs_meta(app_id))
            return [], exact_required_meta, None

        no_rating_meta = build_appmagic_no_rating_data_meta(
            united_app,
            app_id,
            app_name,
            appmagic_url,
            reason,
        )
        no_rating_meta.update(build_app_total_installs_meta(app_id))
        return [], no_rating_meta, None

    # ---- Public fallback (no App Magic auth) ----
    # Collect every country App Magic exposes data for, then fetch the public
    # per-country app-info so we can rank TOP GEO by REAL downloads instead of
    # the arbitrary order returned in `dataCountries`. We keep ALL countries so
    # the global total + "Other" bucket match App Magic's public split closely.
    candidate_iso2 = []
    seen = set()
    for iso2 in united_app.get("dataCountries") or []:
        iso2 = (iso2 or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", iso2) or iso2 == "WW" or iso2 in seen:
            continue
        seen.add(iso2)
        candidate_iso2.append(iso2)
        if len(candidate_iso2) >= APPMAGIC_MAX_INFO_COUNTRIES:
            break

    if not candidate_iso2:
        no_downloads_meta = {
            "appmagic_app_id": united_app.get("id"),
            "appmagic_app_name": app_name,
            "appmagic_url": appmagic_url,
            "raw_data_countries": united_app.get("dataCountries") or [],
            "appmagic_data_countries_error": exact_error,
        }
        no_downloads_meta.update(build_app_total_installs_meta(app_id))
        return [], no_downloads_meta, "APPMAGIC_NO_DOWNLOADS_TOP_GEOS"

    order_by_iso2 = {iso2: idx for idx, iso2 in enumerate(candidate_iso2)}

    def info_task(iso2: str):
        info, info_error = fetch_appmagic_app_info(app_id, iso2)
        country_name, hl = get_country_meta_by_iso2(iso2)
        downloads = None
        appmagic_country_url = None

        if isinstance(info, dict):
            downloads = to_int_or_none(info.get("downloads"))
            country_name = country_name if country_name != iso2 else (info.get("country") or iso2)
            appmagic_country_url = info.get("application_url")
            if appmagic_country_url:
                q = parse_qs(urlparse(appmagic_country_url).query)
                hl = q.get("hl", [hl])[0] or hl

        return {
            "country": country_name,
            "gl": iso2,
            "hl": hl,
            "appmagic_downloads": downloads,
            "appmagic_error": info_error,
            "appmagic_url": appmagic_url,
            "appmagic_country_url": appmagic_country_url,
        }

    all_infos = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS_APPMAGIC) as ex:
        futures = [ex.submit(info_task, iso2) for iso2 in candidate_iso2]
        for f in as_completed(futures):
            all_infos.append(f.result())

    # Decide whether app-info gave us genuine per-country numbers. We only trust
    # download-based ranking when there are several distinct positive values;
    # otherwise we fall back to App Magic's own dataCountries order.
    positive = [i for i in all_infos if (i.get("appmagic_downloads") or 0) > 0]
    distinct_values = {i["appmagic_downloads"] for i in positive}
    has_country_values = len(positive) >= 3 and len(distinct_values) >= 3

    if has_country_values:
        all_infos.sort(key=lambda i: (i.get("appmagic_downloads") or 0), reverse=True)
    else:
        all_infos.sort(key=lambda i: order_by_iso2.get(i["gl"], 9999))

    for idx, info in enumerate(all_infos, start=1):
        info["appmagic_rank"] = idx

    geos = all_infos[:APPMAGIC_TOP_GEO_LIMIT]

    appmagic_meta = {
        "appmagic_app_id": united_app.get("id"),
        "appmagic_app_name": app_name,
        "appmagic_url": appmagic_url,
        "raw_data_countries": candidate_iso2,
        "appmagic_all_country_infos": all_infos,
        "appmagic_has_country_values": has_country_values,
        "appmagic_data_countries_error": exact_error,
        "appmagic_has_exact_country_split": False,
        "appmagic_top_geo_limit": APPMAGIC_TOP_GEO_LIMIT,
    }
    if has_country_values:
        app_total = sum(i.get("appmagic_downloads") or 0 for i in all_infos) or None
        appmagic_meta.update({
            "app_total_installs": app_total,
            "app_total_installs_label": format_appmagic_downloads_label(app_total),
            "app_total_installs_source": "appmagic_country_values_total",
            "app_total_installs_source_label": "App Magic country values",
        })
    else:
        appmagic_meta.update(build_app_total_installs_meta(
            app_id,
            app_info_downloads=[
                info.get("appmagic_downloads")
                for info in all_infos
            ],
        ))

    return geos, appmagic_meta, None


def format_appmagic_downloads_label(total_downloads):
    try:
        total = int(round(float(total_downloads)))
    except Exception:
        return "—"

    if total <= 0:
        return "—"
    if total < 5000:
        return "< 5 000"

    if total < 100000:
        step = 5000
    elif total < 1000000:
        step = 50000
    else:
        step = 100000

    floor_value = max(step, (total // step) * step)
    return f"> {floor_value:,}".replace(",", " ")


def format_google_play_install_bucket_label(gp_range: dict | None) -> str:
    label = (gp_range or {}).get("label") or (gp_range or {}).get("short_label") or ""
    return str(label).replace(",", " ").strip() or "—"


def build_app_total_installs_meta(
    app_id: str,
    app_info_downloads: list[int | None] | None = None,
    data_countries_total=None,
    prefer_appmagic: bool = True,
) -> dict:
    if prefer_appmagic:
        app_info_values = [
            value for value in (app_info_downloads or [])
            if isinstance(value, int) and value > 1
        ]

        if not app_info_values:
            for iso2 in APPMAGIC_TOTAL_INFO_COUNTRIES:
                info, _ = fetch_appmagic_app_info(app_id, iso2)
                downloads = to_int_or_none(info.get("downloads")) if isinstance(info, dict) else None
                if downloads and downloads > 1:
                    app_info_values.append(downloads)
                    break

        if app_info_values:
            total = max(app_info_values)
            return {
                "app_total_installs": total,
                "app_total_installs_label": format_exact_downloads_label(total),
                "app_total_installs_source": "appmagic_app_info",
                "app_total_installs_source_label": "App Magic app-info",
            }

        data_total = to_int_or_none(data_countries_total)
        if data_total:
            return {
                "app_total_installs": data_total,
                "app_total_installs_label": format_appmagic_downloads_label(data_total),
                "app_total_installs_source": "appmagic_data_countries",
                "app_total_installs_source_label": "App Magic data-countries",
            }

    gp_range = fetch_google_play_install_range(app_id)
    if gp_range and gp_range.get("min_installs"):
        return {
            "app_total_installs": int(gp_range["min_installs"]),
            "app_total_installs_label": format_google_play_install_bucket_label(gp_range),
            "app_total_installs_source": "google_play_public_bucket",
            "app_total_installs_source_label": "Google Play public installs",
        }

    return {
        "app_total_installs": None,
        "app_total_installs_label": "—",
        "app_total_installs_source": "none",
        "app_total_installs_source_label": None,
    }


# ---------------- SENSOR TOWER OVERVIEW ----------------

def normalize_android_package_input(raw_value: str) -> str:
    raw = (raw_value or "").strip()
    if not raw:
        return ""

    parsed = urlparse(raw)
    if parsed.netloc:
        host = parsed.netloc.lower()
        if "play.google.com" in host:
            return extract_google_play_app_id(raw)
        if "appmagic.rocks" in host:
            return extract_appmagic_google_play_app_id(raw)
        if "sensortower.com" in host:
            parts = [part for part in parsed.path.split("/") if part]
            if "overview" in parts:
                idx = parts.index("overview")
                if len(parts) > idx + 1:
                    return re.sub(r"[^a-zA-Z0-9._]", "", parts[idx + 1])
            for part in reversed(parts):
                if "." in part:
                    return re.sub(r"[^a-zA-Z0-9._]", "", part)

    match = re.search(r"\b([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)\b", raw)
    return match.group(1) if match else ""


def normalize_sensor_tower_country(raw_country: str | None) -> str:
    country = (raw_country or "US").strip().upper()
    if len(country) == 2 and country.isalpha():
        return country
    resolved = resolve_country_for_geo_link(country)
    if resolved:
        return resolved[0].upper()
    return "US"


def build_sensor_tower_overview_url(app_id: str, country: str) -> str:
    return SENSOR_TOWER_APP_URL.format(app_id=app_id, country=country.upper())


def build_sensor_tower_app_api_url(app_id: str, country: str) -> str:
    return f"{SENSOR_TOWER_APP_API_URL.format(app_id=app_id)}?country={country.upper()}"


def normalize_sensor_tower_os(raw_os: str | None) -> str:
    os_name = (raw_os or "android").strip().lower()
    return os_name if os_name in {"android", "ios"} else "android"


def normalize_sensor_tower_publisher_id(raw_value: str) -> str:
    raw = (raw_value or "").strip()
    if not raw:
        return ""

    parsed = urlparse(raw)
    if parsed.netloc and "sensortower.com" in parsed.netloc:
        parts = [part for part in parsed.path.split("/") if part]
        if "publisher" in parts:
            idx = parts.index("publisher")
            if len(parts) > idx + 2:
                raw = parts[idx + 2]

    raw = unquote(raw).strip()
    raw = re.sub(r"\s+", "+", raw)
    return quote(raw, safe="+._-")


def build_sensor_tower_publisher_url(os_name: str, publisher_id: str) -> str:
    os_name = normalize_sensor_tower_os(os_name)
    publisher_id = normalize_sensor_tower_publisher_id(publisher_id)
    return SENSOR_TOWER_PUBLISHER_URL.format(os=os_name, publisher_id=publisher_id)


def sensor_tower_headers(referer: str) -> dict:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
    }


def format_ms_date(value) -> str:
    number = to_number(value)
    if not number:
        return "—"
    try:
        return datetime.fromtimestamp(number / 1000, tz=timezone.utc).strftime("%Y/%m/%d")
    except Exception:
        return "—"


def parse_sensor_tower_datetime(value) -> datetime | None:
    if value is None:
        return None

    number = to_number(value)
    if number is not None and not isinstance(value, str):
        timestamp = number / 1000 if number > 10_000_000_000 else number
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except Exception:
            return None

    text = str(value).strip()
    if not text or text == "-":
        return None

    candidates = [text]
    if text.endswith("Z"):
        candidates.append(f"{text[:-1]}+00:00")
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            pass

    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass

    return None


def format_sensor_tower_date(value) -> str:
    parsed = parse_sensor_tower_datetime(value)
    return parsed.strftime("%Y/%m/%d") if parsed else "—"


def plural_en(value: int, singular: str, plural: str) -> str:
    return singular if value == 1 else plural


def format_sensor_tower_relative_date(value) -> str:
    parsed = parse_sensor_tower_datetime(value)
    if not parsed:
        return "—"

    days = max(0, (datetime.now(timezone.utc) - parsed).days)
    if days == 0:
        return "today"
    if days < 30:
        return f"{days} {plural_en(days, 'day', 'days')} ago"
    months = max(1, days // 30)
    if months < 12:
        return f"{months} {plural_en(months, 'month', 'months')} ago"
    years = max(1, days // 365)
    return f"{years} {plural_en(years, 'year', 'years')} ago"


def format_sensor_tower_category(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if text.startswith("game_"):
        return f"Game - {text[5:].replace('_', ' ').title()}"
    return text.replace("_", " ").title()


def format_sensor_tower_humanized_metric(metric: dict | None, value_key: str) -> dict:
    if not isinstance(metric, dict):
        return {"value": None, "label": "—", "exact": "—"}

    number = to_int_or_none(metric.get(value_key) or metric.get("value"))
    label = (metric.get("string") or "").strip()
    if not label and number:
        label = format_number_compact(number)
    label = label.replace("k", "K").replace("m", "M").replace("b", "B")

    return {
        "value": number,
        "label": label or "—",
        "exact": format_number_plain(number),
    }


def format_sensor_tower_publisher_price(value) -> str:
    number = to_number(value)
    if number is None:
        return "—"
    if number == 0:
        return "Free"
    if float(number).is_integer():
        return f"${int(number)}"
    return f"${number:.2f}"


def format_sensor_tower_publisher_app(row: dict | None, country: str) -> dict | None:
    if not isinstance(row, dict):
        return None

    app_id = (row.get("app_id") or row.get("id") or "").strip()
    if not app_id:
        return None

    downloads = format_sensor_tower_humanized_metric(
        row.get("humanized_worldwide_last_30_days_downloads"),
        "downloads",
    )
    return {
        "app_id": app_id,
        "name": row.get("humanized_name") or row.get("name") or app_id,
        "publisher_name": row.get("publisher_name") or "",
        "publisher_id": row.get("publisher_id") or "",
        "icon_url": row.get("icon_url") or "",
        "active": bool(row.get("active")),
        "os": row.get("os") or "android",
        "price": format_sensor_tower_publisher_price(row.get("price")),
        "downloads_last_30_days": downloads,
        "release_date": format_sensor_tower_date(row.get("release_date")),
        "last_update": format_sensor_tower_relative_date(row.get("updated_date")),
        "last_update_date": format_sensor_tower_date(row.get("updated_date")),
        "canonical_country": (row.get("canonical_country") or "").upper(),
        "can_open": "." in app_id,
        "google_play_url": build_google_play_url(app_id, country, GOOGLE_PLAY_DEFAULT_INSTALL_LANG),
        "sensor_tower_url": build_sensor_tower_overview_url(app_id, country),
    }


def format_number_plain(value) -> str:
    number = to_int_or_none(value)
    if not number:
        return "—"
    return f"{number:,}".replace(",", " ")


def format_number_compact(value) -> str:
    number = to_int_or_none(value)
    if not number:
        return "—"
    if number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.1f}B".replace(".0B", "B")
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M".replace(".0M", "M")
    if number >= 1_000:
        return f"{number / 1_000:.1f}K".replace(".0K", "K")
    return str(number)


def format_currency_value(value, currency: str = "USD", unit: str | None = None) -> dict:
    number = to_number(value)
    if number is None:
        return {"value": None, "label": "—", "exact": "—"}

    amount = number / 100 if unit == "cent" else number
    symbol = "$" if (currency or "").upper() == "USD" else f"{(currency or '').upper()} "
    label = f"{symbol}{amount:,.0f}".replace(",", " ")
    return {"value": amount, "label": f"≈ {label}", "exact": label}


def format_sensor_tower_metric(metric: dict | None) -> dict:
    if not isinstance(metric, dict):
        return {"value": None, "label": "—", "exact": "—"}

    value = metric.get("value")
    if metric.get("type") == "currency" or metric.get("unit") == "cent":
        return format_currency_value(value, metric.get("currency") or "USD", metric.get("unit"))

    number = to_int_or_none(value)
    if not number:
        return {"value": None, "label": "—", "exact": "—"}

    return {
        "value": number,
        "label": format_number_compact(number),
        "exact": format_number_plain(number),
    }


def format_sensor_tower_price(price: dict | None) -> str:
    if not isinstance(price, dict):
        return "—"
    value = to_number(price.get("value"))
    if value == 0:
        return "Free"
    return (price.get("string_value") or "").strip() or "—"


def sensor_tower_description_to_text(value: str | None) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(value.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n"), "html.parser")
    lines = [line.rstrip() for line in soup.get_text("\n").splitlines()]
    compacted = []
    previous_blank = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if not previous_blank and compacted:
                compacted.append("")
            previous_blank = True
            continue
        compacted.append(stripped)
        previous_blank = False
    return "\n".join(compacted).strip()


def sensor_tower_description_payload(data: dict | None, source_country: str) -> dict:
    description = (data or {}).get("description") or {}
    if not isinstance(description, dict):
        description = {}
    return {
        "short": sensor_tower_description_to_text(description.get("short_description")),
        "full": sensor_tower_description_to_text(description.get("full_description")),
        "source_country": normalize_sensor_tower_country(source_country),
    }


def fetch_sensor_tower_english_description(app_id: str, country: str, current_data: dict) -> dict:
    country = normalize_sensor_tower_country(country)
    candidates: list[str] = []

    if country in SENSOR_TOWER_ENGLISH_DESCRIPTION_COUNTRIES:
        candidates.append(country)
    candidates.extend(SENSOR_TOWER_ENGLISH_DESCRIPTION_COUNTRIES)
    candidates.append(country)

    seen = set()
    for candidate in candidates:
        candidate = normalize_sensor_tower_country(candidate)
        if candidate in seen:
            continue
        seen.add(candidate)

        data = current_data if candidate == country else None
        if data is None:
            data, _ = fetch_sensor_tower_app_overview(app_id, candidate)
        payload = sensor_tower_description_payload(data, candidate)
        if payload["short"] or payload["full"]:
            return payload

    return {"short": "", "full": "", "source_country": country}


def top_country_payload(country_codes: list[str] | None) -> list[dict]:
    rows = []
    for code in country_codes or []:
        iso2 = (code or "").upper()
        if len(iso2) != 2:
            continue
        name, _ = get_country_meta_by_iso2(iso2)
        rows.append({"code": iso2, "name": name})
    return rows


def sensor_tower_release_status_label(status: str | None) -> str:
    status = (status or "").replace("_", " ").strip().title()
    return status or "—"


def fetch_sensor_tower_app_overview(app_id: str, country: str) -> tuple[dict | None, str | None]:
    country = normalize_sensor_tower_country(country)
    cache_key = ("sensor_tower_app", app_id.lower(), country)
    cached = SENSOR_TOWER_APP_CACHE.get(cache_key)
    if cached is not CACHE_MISS:
        return cached

    api_url = build_sensor_tower_app_api_url(app_id, country)
    headers = sensor_tower_headers(build_sensor_tower_overview_url(app_id, country))

    try:
        r = session.get(api_url, headers=headers, timeout=SENSOR_TOWER_TIMEOUT)
    except Exception as e:
        return None, f"SENSOR_TOWER_ERROR:{e}"

    if r.status_code != 200:
        return None, f"SENSOR_TOWER_HTTP_{r.status_code}"

    try:
        data = r.json()
    except Exception:
        return None, "SENSOR_TOWER_BAD_JSON"

    if not isinstance(data, dict) or data.get("error"):
        return None, data.get("error") if isinstance(data, dict) else "SENSOR_TOWER_BAD_JSON"

    result = (data, None)
    SENSOR_TOWER_APP_CACHE.set(cache_key, result)
    return result


def fetch_sensor_tower_publisher_metadata(os_name: str, publisher_id: str) -> tuple[dict | None, str | None]:
    os_name = normalize_sensor_tower_os(os_name)
    publisher_id = normalize_sensor_tower_publisher_id(publisher_id)
    cache_key = ("sensor_tower_publisher_metadata", os_name, publisher_id)
    cached = SENSOR_TOWER_PUBLISHER_METADATA_CACHE.get(cache_key)
    if cached is not CACHE_MISS:
        return cached

    url = SENSOR_TOWER_PUBLISHER_METADATA_API_URL.format(os=os_name, publisher_id=publisher_id)

    try:
        r = session.get(
            url,
            headers=sensor_tower_headers(build_sensor_tower_publisher_url(os_name, publisher_id)),
            timeout=SENSOR_TOWER_TIMEOUT,
        )
    except Exception as e:
        return None, f"SENSOR_TOWER_PUBLISHER_METADATA_ERROR:{e}"

    if r.status_code != 200:
        return None, f"SENSOR_TOWER_PUBLISHER_METADATA_HTTP_{r.status_code}"

    try:
        data = r.json()
    except Exception:
        return None, "SENSOR_TOWER_PUBLISHER_METADATA_BAD_JSON"

    if not isinstance(data, dict) or data.get("error"):
        return None, data.get("error") if isinstance(data, dict) else "SENSOR_TOWER_PUBLISHER_METADATA_BAD_JSON"

    result = (data, None)
    SENSOR_TOWER_PUBLISHER_METADATA_CACHE.set(cache_key, result)
    return result


def fetch_sensor_tower_publisher_apps(
    os_name: str,
    publisher_id: str,
    limit: int,
    offset: int,
    sort_by: str,
) -> tuple[dict | None, str | None]:
    os_name = normalize_sensor_tower_os(os_name)
    publisher_id = normalize_sensor_tower_publisher_id(publisher_id)
    sort_by = sort_by if sort_by in {"downloads", "revenue"} else "downloads"
    cache_key = ("sensor_tower_publisher_apps", os_name, publisher_id, int(limit), int(offset), sort_by)
    cached = SENSOR_TOWER_PUBLISHER_APPS_CACHE.get(cache_key)
    if cached is not CACHE_MISS:
        return cached

    url = SENSOR_TOWER_PUBLISHER_APPS_API_URL.format(os=os_name, publisher_id=publisher_id)
    params = {"limit": limit, "offset": offset, "sort_by": sort_by}

    try:
        r = session.get(
            url,
            params=params,
            headers=sensor_tower_headers(build_sensor_tower_publisher_url(os_name, publisher_id)),
            timeout=SENSOR_TOWER_TIMEOUT,
        )
    except Exception as e:
        return None, f"SENSOR_TOWER_PUBLISHER_APPS_ERROR:{e}"

    if r.status_code != 200:
        return None, f"SENSOR_TOWER_PUBLISHER_APPS_HTTP_{r.status_code}"

    try:
        data = r.json()
    except Exception:
        return None, "SENSOR_TOWER_PUBLISHER_APPS_BAD_JSON"

    if not isinstance(data, dict) or data.get("error"):
        return None, data.get("error") if isinstance(data, dict) else "SENSOR_TOWER_PUBLISHER_APPS_BAD_JSON"

    result = (data, None)
    SENSOR_TOWER_PUBLISHER_APPS_CACHE.set(cache_key, result)
    return result


def build_sensor_tower_publisher_payload(
    publisher_id: str,
    country: str,
    os_name: str = "android",
    limit: int = SENSOR_TOWER_PUBLISHER_APPS_LIMIT,
    offset: int = 0,
    sort_by: str = "downloads",
) -> tuple[dict | None, str | None]:
    country = normalize_sensor_tower_country(country)
    os_name = normalize_sensor_tower_os(os_name)
    publisher_id = normalize_sensor_tower_publisher_id(publisher_id)
    if not publisher_id:
        return None, "SENSOR_TOWER_PUBLISHER_ID_REQUIRED"

    sort_by = sort_by if sort_by in {"downloads", "revenue"} else "downloads"
    cache_key = ("publisher_payload", publisher_id, country, os_name, int(limit), int(offset), sort_by)
    cached = PUBLISHER_PAYLOAD_CACHE.get(cache_key)
    if cached is not CACHE_MISS:
        return cached

    with ThreadPoolExecutor(max_workers=2) as executor:
        metadata_future = executor.submit(fetch_sensor_tower_publisher_metadata, os_name, publisher_id)
        apps_future = executor.submit(fetch_sensor_tower_publisher_apps, os_name, publisher_id, limit, offset, sort_by)
        metadata, metadata_error = metadata_future.result()
        apps_response, apps_error = apps_future.result()

    if metadata_error:
        return None, metadata_error

    apps_rows = (apps_response or {}).get("data") if isinstance(apps_response, dict) else []
    apps = [
        formatted
        for formatted in (format_sensor_tower_publisher_app(row, country) for row in (apps_rows or []))
        if formatted
    ]

    total_apps = metadata.get("total_apps") if isinstance(metadata.get("total_apps"), dict) else {}
    icon_urls = [url for url in (metadata.get("icon_urls") or []) if isinstance(url, str)]
    downloads = format_sensor_tower_humanized_metric(
        metadata.get("humanized_worldwide_last_30_days_downloads"),
        "downloads",
    )
    most_downloaded = format_sensor_tower_publisher_app(metadata.get("most_downloaded_app"), country)
    top_categories = [
        formatted
        for formatted in (format_sensor_tower_category(item) for item in (metadata.get("top_categories") or []))
        if formatted
    ]

    payload = {
        "publisher": {
            "id": metadata.get("publisher_id") or publisher_id,
            "name": metadata.get("publisher_name") or unquote(publisher_id).replace("+", " "),
            "country": metadata.get("publisher_country") or "—",
            "profile_url": build_sensor_tower_publisher_url(os_name, metadata.get("publisher_id") or publisher_id),
            "icon_urls": icon_urls[:6],
            "top_categories": top_categories,
            "top_countries": top_country_payload(metadata.get("top_countries")),
            "total_apps": {
                "total": to_int_or_none(total_apps.get("total")) or 0,
                "active": to_int_or_none(total_apps.get("active")) or 0,
                "inactive": to_int_or_none(total_apps.get("inactive")) or 0,
            },
            "downloads_last_30_days": downloads,
            "most_downloaded_app": most_downloaded,
            "company_website": metadata.get("company_website") or "",
            "headquarters": metadata.get("publisher_headquarters") or "",
        },
        "apps": apps,
        "meta": {
            "count": ((apps_response or {}).get("meta") or {}).get("count", len(apps)) if isinstance(apps_response, dict) else len(apps),
            "limit": limit,
            "offset": offset,
            "sort_by": sort_by if sort_by in {"downloads", "revenue"} else "downloads",
            "apps_error": apps_error,
            "source": "Sensor Tower public publisher endpoints",
        },
    }
    result = (payload, None)
    PUBLISHER_PAYLOAD_CACHE.set(cache_key, result)
    return result


def future_result(future, default=None):
    try:
        return future.result()
    except Exception:
        return default


def build_google_play_overview_availability(app_id: str, sensor_tower_data: dict | None = None) -> dict:
    app_id = (app_id or "").strip()
    cache_key = app_id.lower()
    now = time.time()

    with OVERVIEW_AVAILABILITY_CACHE_LOCK:
        cached = OVERVIEW_AVAILABILITY_CACHE.get(cache_key)
        if cached and now - cached.get("created_at", 0) < OVERVIEW_AVAILABILITY_CACHE_TTL:
            payload = dict(cached["payload"])
            payload["cached"] = True
            return payload

    geo = get_geo_countries_en()
    rows = check_availability_google(app_id, geo)
    available_rows = [row for row in rows if row.get("available") is True]
    closed_rows = [row for row in rows if row.get("available") is False]
    error_rows = [row for row in rows if row.get("error") and row.get("available") is not True]

    sensor_tower_data = sensor_tower_data or {}
    sensor_tower_available_codes = [
        code.upper()
        for code in (sensor_tower_data.get("available_countries") or [])
        if isinstance(code, str)
    ]
    sensor_tower_valid_codes = [
        code.upper()
        for code in (sensor_tower_data.get("valid_countries") or [])
        if isinstance(code, str)
    ]

    payload = {
        "source": "Google Play install availability checker",
        "source_label": "Google Play availability",
        "checked_by": "availability_page",
        "countries_count": len(geo),
        "available_count": len(available_rows),
        "closed_count": len(closed_rows),
        "error_count": len(error_rows),
        "available_countries": top_country_payload([row["iso2"] for row in available_rows[:24]]),
        "closed_countries": top_country_payload([row["iso2"] for row in closed_rows[:24]]),
        "rows": rows,
        "cached": False,
        "checked_at": int(now),
        "sensor_tower_available_count": len(sensor_tower_available_codes),
        "sensor_tower_valid_count": len(sensor_tower_valid_codes),
        "sensor_tower_available_countries": top_country_payload(sensor_tower_available_codes[:24]),
        "pre_order_countries": top_country_payload(sensor_tower_data.get("pre_order_countries"))[:24],
    }

    with OVERVIEW_AVAILABILITY_CACHE_LOCK:
        OVERVIEW_AVAILABILITY_CACHE[cache_key] = {"created_at": now, "payload": payload}

    return payload


def build_app_overview_payload(app_id: str, country: str) -> tuple[dict | None, str | None]:
    app_id = normalize_android_package_input(app_id) or (app_id or "").strip()
    country = normalize_sensor_tower_country(country)
    cache_key = ("app_overview_payload", app_id.lower(), country)
    cached = APP_OVERVIEW_PAYLOAD_CACHE.get(cache_key)
    if cached is not CACHE_MISS:
        return cached

    data, error = fetch_sensor_tower_app_overview(app_id, country)
    if error:
        return None, error

    app_id = data.get("app_id") or app_id
    with ThreadPoolExecutor(max_workers=3) as executor:
        description_future = executor.submit(fetch_sensor_tower_english_description, app_id, country, data)
        availability_future = executor.submit(build_google_play_overview_availability, app_id, data)
        install_range_future = (
            None if data.get("installs")
            else executor.submit(fetch_google_play_install_range, app_id)
        )

    screenshots = data.get("screenshots") or {}
    android_screenshots = screenshots.get("android") if isinstance(screenshots, dict) else []
    trailers = data.get("trailers") or {}
    android_trailers = trailers.get("android") if isinstance(trailers, dict) else []

    downloads_last_month = format_sensor_tower_metric(data.get("worldwide_last_month_downloads"))
    google_play_url = build_google_play_url(app_id, country, GOOGLE_PLAY_DEFAULT_INSTALL_LANG)
    sensor_tower_url = build_sensor_tower_overview_url(app_id, country)

    categories = [
        (item.get("name") or item.get("id") or "").strip()
        for item in (data.get("categories") or [])
        if isinstance(item, dict) and (item.get("name") or item.get("id"))
    ]

    description_payload = future_result(
        description_future,
        {"short": "", "full": "", "source_country": country},
    )
    availability_payload = future_result(availability_future, {})
    install_range_payload = future_result(install_range_future) if install_range_future else None

    payload = {
        "app_id": app_id,
        "country": country,
        "country_name": get_country_meta_by_iso2(country)[0],
        "name": data.get("name") or app_id,
        "publisher_name": data.get("publisher_name") or "—",
        "publisher_id": data.get("publisher_id") or "",
        "publisher_country": data.get("publisher_country") or "—",
        "publisher_profile_url": (
            f"https://app.sensortower.com{data.get('publisher_profile_url')}"
            if data.get("publisher_profile_url") else ""
        ),
        "icon_url": data.get("icon_url") or "",
        "google_play_url": google_play_url,
        "sensor_tower_url": sensor_tower_url,
        "support_url": data.get("support_url") or "",
        "website_url": data.get("website_url") or "",
        "categories": categories,
        "price": format_sensor_tower_price(data.get("price")),
        "install_range": data.get("installs") or format_google_play_install_bucket_label(install_range_payload),
        "downloads_last_month": downloads_last_month,
        "content_rating": data.get("content_rating") or "—",
        "rating": data.get("rating"),
        "rating_count": to_int_or_none(data.get("rating_count")) or 0,
        "has_in_app_purchases": bool(data.get("has_in_app_purchases")),
        "advertised_on_any_network": (data.get("advertised_on_any_network") or {}).get("value") or "—",
        "release_status": sensor_tower_release_status_label(data.get("release_status")),
        "release_details": {
            "current_version": data.get("current_version") or "—",
            "last_updated": format_ms_date(data.get("recent_release_date") or data.get("release_date")),
            "country_release_date": format_ms_date(data.get("country_release_date")),
            "worldwide_release_date": format_ms_date(data.get("worldwide_release_date")),
            "publisher_country": data.get("publisher_country") or "—",
            "minimum_os_version": data.get("minimum_os_version") or "—",
            "file_size": data.get("file_size") or "—",
            "first_released_in": top_country_payload(data.get("first_released_in"))[:8],
        },
        "description": description_payload,
        "media": {
            "feature_graphic": data.get("feature_graphic") or "",
            "screenshots": android_screenshots if isinstance(android_screenshots, list) else [],
            "trailers": android_trailers if isinstance(android_trailers, list) else [],
        },
        "top_countries": top_country_payload(data.get("top_countries")),
        "availability": availability_payload,
        "category_rankings": data.get("category_rankings") or {},
        "source": {
            "primary": "Sensor Tower public app endpoint",
            "api_url": build_sensor_tower_app_api_url(app_id, country),
        },
    }

    result = (payload, None)
    APP_OVERVIEW_PAYLOAD_CACHE.set(cache_key, result)
    return result


def round_percentages_to_100(shares: list[float]) -> list[int]:
    if not shares:
        return []

    raw = [max(0.0, share) * 100 for share in shares]
    floors = [int(value) for value in raw]
    remainder = 100 - sum(floors)

    fractions = sorted(
        enumerate(value - int(value) for value in raw),
        key=lambda item: item[1],
        reverse=True,
    )

    idx = 0
    while remainder > 0 and fractions:
        floors[fractions[idx % len(fractions)][0]] += 1
        remainder -= 1
        idx += 1

    idx = 0
    shrink_candidates = [
        item for item in sorted(
            enumerate(value - int(value) for value in raw),
            key=lambda item: item[1],
        )
        if floors[item[0]] > 0
    ]
    while remainder < 0 and shrink_candidates:
        target_idx = shrink_candidates[idx % len(shrink_candidates)][0]
        if floors[target_idx] > 0:
            floors[target_idx] -= 1
            remainder += 1
        idx += 1

    return floors


def _empty_appmagic_download_meta():
    empty_periods = {
        period: {
            "label": config["label"],
            "downloads_total": None,
            "downloads_label": "—",
            "downloads_distribution": [],
        }
        for period, config in APPMAGIC_PERIOD_FIELDS.items()
    }
    return {
        "downloads_total": None,
        "downloads_label": "—",
        "downloads_distribution": [],
        "downloads_periods": empty_periods,
        "downloads_period_active": "last30days",
        "downloads_estimate_source": "none",
    }


def enrich_appmagic_download_estimates(
    geos: list[dict],
    all_country_infos: list[dict] | None = None,
    has_country_values: bool = False,
    top_limit: int = APPMAGIC_TOP_GEO_LIMIT,
) -> tuple[list[dict], dict]:
    if not geos:
        return geos, _empty_appmagic_download_meta()

    use_country_values = bool(has_country_values and all_country_infos)

    if use_country_values:
        # Real per-country downloads from App Magic public app-info.
        # Total is across ALL countries App Magic exposes, so TOP GEO shares are
        # a share of the global figure and the remainder collapses into "Other"
        # — exactly how App Magic presents the public download split.
        global_total = sum((i.get("appmagic_downloads") or 0) for i in all_country_infos)
        top_sum = sum((g.get("appmagic_downloads") or 0) for g in geos)
        other_downloads = max(0, global_total - top_sum)
        estimate_source = "appmagic_country_values"

        fracs = [
            ((g.get("appmagic_downloads") or 0) / global_total) if global_total else 0.0
            for g in geos
        ]
        other_present = other_downloads > 0 and global_total > 0
        if other_present:
            fracs.append(other_downloads / global_total)

        percents = round_percentages_to_100(fracs)

        distribution = []
        for idx, geo in enumerate(geos):
            share = percents[idx]
            installs = geo.get("appmagic_downloads")
            geo["appmagic_share"] = share
            geo["appmagic_estimated_installs"] = installs
            geo["appmagic_periods"] = {
                period: {"share": share, "estimated_installs": installs}
                for period in APPMAGIC_PERIOD_FIELDS
            }
            distribution.append({
                "country": geo.get("country"),
                "gl": geo.get("gl"),
                "rank": geo.get("appmagic_rank"),
                "share": share,
                "estimated_installs": installs,
                "color": APPMAGIC_COLORS[idx % len(APPMAGIC_COLORS)],
                "is_other": False,
            })

        if other_present:
            distribution.append({
                "country": "Other",
                "gl": None,
                "rank": None,
                "share": percents[-1],
                "estimated_installs": other_downloads,
                "color": "#c4c4c4",
                "is_other": True,
            })

        total_downloads = global_total

    else:
        # No usable per-country values → rank-weighted estimate over the TOP GEO.
        numeric_downloads = [
            float(geo["appmagic_downloads"])
            for geo in geos
            if isinstance(geo.get("appmagic_downloads"), (int, float)) and geo["appmagic_downloads"] > 0
        ]
        total_downloads = max(numeric_downloads) if numeric_downloads else None
        weights = [
            1 / ((geo.get("appmagic_rank") or idx + 1) ** APPMAGIC_RANK_ESTIMATE_EXPONENT)
            for idx, geo in enumerate(geos)
        ]
        estimate_source = "appmagic_rank_estimate"

        total_weight = sum(weights) or 1
        shares = [weight / total_weight for weight in weights]
        percents = round_percentages_to_100(shares)

        distribution = []
        for idx, (geo, share, percent) in enumerate(zip(geos, shares, percents), start=1):
            estimated_installs = int(round(total_downloads * share)) if total_downloads else None
            geo["appmagic_share"] = percent
            geo["appmagic_estimated_installs"] = estimated_installs
            geo["appmagic_periods"] = {
                period: {"share": percent, "estimated_installs": estimated_installs}
                for period in APPMAGIC_PERIOD_FIELDS
            }
            distribution.append({
                "country": geo.get("country"),
                "gl": geo.get("gl"),
                "rank": geo.get("appmagic_rank"),
                "share": percent,
                "estimated_installs": estimated_installs,
                "color": APPMAGIC_COLORS[(idx - 1) % len(APPMAGIC_COLORS)],
                "is_other": False,
            })

    period_meta = {
        period: {
            "label": config["label"],
            "downloads_total": int(round(total_downloads)) if total_downloads else None,
            "downloads_label": format_appmagic_downloads_label(total_downloads),
            "downloads_distribution": distribution,
        }
        for period, config in APPMAGIC_PERIOD_FIELDS.items()
    }

    return geos, {
        "downloads_total": int(round(total_downloads)) if total_downloads else None,
        "downloads_label": format_appmagic_downloads_label(total_downloads),
        "downloads_distribution": distribution,
        "downloads_periods": period_meta,
        "downloads_period_active": "last30days",
        "downloads_estimate_source": estimate_source,
    }


# ---------------- FETCHERS (INSTALL AVAILABILITY) ----------------

def fetch_google_play_availability(app_id: str, gl: str, hl: str = "en", force_refresh: bool = False):
    cache_key = ("google_availability", app_id.lower(), gl.upper(), hl)
    if not force_refresh:
        cached = GOOGLE_AVAILABILITY_CACHE.get(cache_key)
        if cached is not CACHE_MISS:
            return cached

    time.sleep(random.uniform(GOOGLE_JITTER_MIN, GOOGLE_JITTER_MAX))

    url = build_google_play_url(app_id, gl, hl)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": f"{hl},{hl.split('-')[0]};q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        r = session.get(url, headers=headers, timeout=30, allow_redirects=True)
    except Exception as e:
        return False, f"REQUEST_ERROR:{e}"

    if r.status_code == 404:
        result = (False, "NOT_FOUND")
        GOOGLE_AVAILABILITY_CACHE.set(cache_key, result)
        return result
    if r.status_code in (429, 503):
        return False, f"BLOCKED_HTTP_{r.status_code}"
    if r.status_code != 200:
        return False, f"HTTP_{r.status_code}"

    html = r.text or ""
    low = html.lower()

    if "consent.google.com" in low or "unusual traffic" in low:
        return False, "CONSENT_OR_UNUSUAL_TRAFFIC"

    geo_block_patterns = (
        "not available in your country",
        "isn't available in your country",
        "is not available in your country",
        "not available in your region",
        "isn't available in your region",
        "this item isn't available",
        "item isn't available",
        "this app is not available",
        "this item is not available",
    )
    if any(p in low for p in geo_block_patterns):
        result = (False, "GEO_BLOCKED_TEXT")
        GOOGLE_AVAILABILITY_CACHE.set(cache_key, result)
        return result

    # A) schema.org Offer block referencing this app
    offer_hit = False
    if 'itemprop="offers"' in low and 'itemprop="url"' in low:
        pattern = (
            r'itemprop=["\']url["\'][^>]*content=["\'][^"\']*details\?id='
            + re.escape(app_id.lower())
            + r'[^"\']*["\']'
        )
        if re.search(pattern, low):
            offer_hit = True
    if offer_hit:
        result = (True, "SCHEMA_OFFERS")
        GOOGLE_AVAILABILITY_CACHE.set(cache_key, result)
        return result

    # B) aria-label install variants
    install_aria_variants = (
        'aria-label="install"',
        'aria-label="установить"',
        'aria-label="instalar"',
        'aria-label="installer"',
        'aria-label="installieren"',
        'aria-label="installa"',
        'aria-label="yükle"',
        'aria-label="설치"',
        'aria-label="安装"',
        'aria-label="インストール"',
    )
    if any(v in low for v in install_aria_variants):
        result = (True, "ARIA_INSTALL")
        GOOGLE_AVAILABILITY_CACHE.set(cache_key, result)
        return result

    # C) button contains Install text (even if disabled)
    try:
        buttons = re.findall(r"<button\b[^>]*>.*?</button>", html, flags=re.IGNORECASE | re.DOTALL)
        for blk in buttons[:300]:
            blk_low = blk.lower()
            if ("vfppkd" in blk_low) or ("aria-label" in blk_low) or ("jsaction" in blk_low):
                if ">install<" in blk_low or 'aria-label="install"' in blk_low:
                    result = (True, "BUTTON_INSTALL")
                    GOOGLE_AVAILABILITY_CACHE.set(cache_key, result)
                    return result
                if ">установить<" in blk_low or 'aria-label="установить"' in blk_low:
                    result = (True, "BUTTON_INSTALL_RU")
                    GOOGLE_AVAILABILITY_CACHE.set(cache_key, result)
                    return result
    except Exception:
        pass

    if ('jscontroller="chfswc"' in low) and ('jsaction="jibuqc:' in low):
        result = (True, "CONTROLLER_INSTALL")
        GOOGLE_AVAILABILITY_CACHE.set(cache_key, result)
        return result

    pre_register_patterns = ("pre-register", "preregister", "pre register")
    if any(p in low for p in pre_register_patterns):
        result = (False, "PRE_REGISTER")
        GOOGLE_AVAILABILITY_CACHE.set(cache_key, result)
        return result

    result = (False, "NO_INSTALL_SIGNALS")
    GOOGLE_AVAILABILITY_CACHE.set(cache_key, result)
    return result


def google_availability_error_code(error: str | None) -> str:
    return str(error or "").split(":", 1)[0].strip().upper()


def google_availability_is_closed_error(error: str | None) -> bool:
    return google_availability_error_code(error) in GOOGLE_AVAILABILITY_CLOSED_ERRORS


def fetch_google_play_availability_confirmed(app_id: str, gl: str, primary_hl: str = "en"):
    first_available, first_error = fetch_google_play_availability(app_id, gl, hl=primary_hl)
    should_confirm = (
        AVAILABILITY_CONFIRM_ALL_COUNTRIES
        or first_available is not True
        or google_availability_is_closed_error(first_error)
    )
    if not should_confirm:
        return first_available, first_error

    _country_name, local_hl = get_country_meta_by_iso2(gl)
    confirm_hl = (local_hl or primary_hl or "en").strip() or "en"
    second_available, second_error = fetch_google_play_availability(
        app_id,
        gl,
        hl=confirm_hl,
        force_refresh=True,
    )

    # Any clear open signal wins. Google Play can sometimes return incomplete
    # markup for a country, so a country is closed only after both checks agree
    # with one of the strict closed signals we trust.
    if first_available is True:
        return True, first_error
    if second_available is True:
        return True, second_error
    if google_availability_is_closed_error(first_error) and google_availability_is_closed_error(second_error):
        return False, second_error or first_error
    return None, second_error or first_error


def fetch_apple_store_availability(app_id: str, country_iso2: str):
    cc = country_iso2.lower()
    cache_key = ("apple_availability", str(app_id), cc)
    cached = APPLE_AVAILABILITY_CACHE.get(cache_key)
    if cached is not CACHE_MISS:
        return cached

    url = "https://itunes.apple.com/lookup"
    params = {"id": app_id, "country": cc}
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

    try:
        r = session.get(url, params=params, headers=headers, timeout=30)
    except Exception as e:
        return False, f"REQUEST_ERROR:{e}"

    if r.status_code != 200:
        return False, f"HTTP_{r.status_code}"

    try:
        data = r.json()
    except Exception:
        return False, "BAD_JSON"

    results = data.get("results") or []
    if not results:
        result = (False, "NOT_AVAILABLE")
        APPLE_AVAILABILITY_CACHE.set(cache_key, result)
        return result
    result = (True, None)
    APPLE_AVAILABILITY_CACHE.set(cache_key, result)
    return result


# ---------------- PARALLEL CHECKERS ----------------

def check_google(app_id: str, threshold: float, countries: dict):
    all_rows = []
    below_rows = []

    def task(country, gl, hl):
        rating, error = fetch_google_play_rating(app_id, gl, hl)
        return country, gl, hl, rating, error

    with ThreadPoolExecutor(max_workers=MAX_WORKERS_GOOGLE) as ex:
        futures = [ex.submit(task, country, gl, hl) for country, (gl, hl) in countries.items()]
        for f in as_completed(futures):
            country, gl, hl, rating, error = f.result()
            row = {
                "store": "Google Play",
                "country": country,
                "gl": gl,
                "hl": hl,
                "rating": rating,
                "error": error,
                "play_url": build_google_play_url(app_id, gl, hl),
            }
            all_rows.append(row)
            if rating is not None and rating < threshold:
                below_rows.append(row)

    all_rows.sort(key=lambda r: r["country"])
    below_rows.sort(key=lambda r: r["country"])
    return all_rows, below_rows


def check_google_appmagic(app_id: str, threshold: float):
    appmagic_geos, appmagic_meta, appmagic_error = fetch_appmagic_top_geos(app_id)
    if appmagic_error:
        raise ValueError(appmagic_error)

    if not appmagic_meta.get("downloads_periods"):
        appmagic_geos, downloads_meta = enrich_appmagic_download_estimates(
            appmagic_geos,
            all_country_infos=appmagic_meta.get("appmagic_all_country_infos"),
            has_country_values=appmagic_meta.get("appmagic_has_country_values", False),
            top_limit=appmagic_meta.get("appmagic_top_geo_limit", APPMAGIC_TOP_GEO_LIMIT),
        )
        appmagic_meta.update(downloads_meta)

    # Drop the bulky internal per-country payload before sending to the client.
    appmagic_meta.pop("appmagic_all_country_infos", None)

    all_rows = []
    below_rows = []

    def task(geo):
        rating, error = fetch_google_play_rating(app_id, geo["gl"], geo["hl"])
        return geo, rating, error

    with ThreadPoolExecutor(max_workers=MAX_WORKERS_GOOGLE) as ex:
        futures = [ex.submit(task, geo) for geo in appmagic_geos]
        for f in as_completed(futures):
            geo, rating, error = f.result()
            notes = []
            if geo.get("appmagic_error"):
                notes.append(geo["appmagic_error"])
            if error:
                notes.append(error)

            row = {
                "store": "Google Play",
                "source": "App Magic",
                "country": geo["country"],
                "gl": geo["gl"],
                "hl": geo["hl"],
                "rating": rating,
                "error": " | ".join(notes),
                "play_url": build_google_play_url(app_id, geo["gl"], geo["hl"]),
                "appmagic_rank": geo.get("appmagic_rank"),
                "appmagic_share": geo.get("appmagic_share"),
                "appmagic_estimated_installs": geo.get("appmagic_estimated_installs"),
                "appmagic_downloads": geo.get("appmagic_downloads"),
                "appmagic_periods": geo.get("appmagic_periods"),
                "appmagic_url": geo.get("appmagic_url"),
                "appmagic_country_url": geo.get("appmagic_country_url"),
            }
            all_rows.append(row)
            if rating is not None and rating < threshold:
                below_rows.append(row)

    all_rows.sort(key=lambda r: (r["appmagic_rank"] or 9999, r["country"]))
    below_rows.sort(key=lambda r: (r["appmagic_rank"] or 9999, r["country"]))
    return all_rows, below_rows, appmagic_meta


def check_apple(apple_id: str, threshold: float, l_param: str | None, countries: dict):
    all_rows = []
    below_rows = []

    def task(country, cc, hl):
        rating, error = fetch_apple_store_rating(apple_id, cc)
        return country, cc, hl, rating, error

    with ThreadPoolExecutor(max_workers=MAX_WORKERS_APPLE) as ex:
        futures = [ex.submit(task, country, cc, hl) for country, (cc, hl) in countries.items()]
        for f in as_completed(futures):
            country, cc, hl, rating, error = f.result()
            row = {
                "store": "App Store",
                "country": country,
                "gl": cc,
                "hl": hl,
                "rating": rating,
                "error": error,
                "play_url": build_apple_store_url(apple_id, cc, l_param),
            }
            all_rows.append(row)
            if rating is not None and rating < threshold:
                below_rows.append(row)

    all_rows.sort(key=lambda r: r["country"])
    below_rows.sort(key=lambda r: r["country"])
    return all_rows, below_rows


def check_availability_google(app_id: str, countries_en: list[tuple[str, str]]):
    rows = []

    def task(country_name: str, iso2: str):
        available, error = fetch_google_play_availability_confirmed(app_id, iso2, primary_hl="en")
        return country_name, iso2, available, error

    with ThreadPoolExecutor(max_workers=MAX_WORKERS_AVAIL_GOOGLE) as ex:
        futures = [ex.submit(task, name, iso2) for name, iso2 in countries_en]
        for f in as_completed(futures):
            country_name, iso2, available, error = f.result()
            rows.append({
                "store": "Google Play",
                "country": country_name,
                "iso2": iso2,
                "available": available,
                "error": error,
                "store_url": build_google_play_url(app_id, iso2, "en"),
            })

    rows.sort(key=lambda r: r["country"])
    return rows


def check_availability_apple(app_id: str, l_param: str | None, countries_en: list[tuple[str, str]]):
    rows = []

    def task(country_name: str, iso2: str):
        available, error = fetch_apple_store_availability(app_id, iso2)
        return country_name, iso2, available, error

    with ThreadPoolExecutor(max_workers=MAX_WORKERS_AVAIL_APPLE) as ex:
        futures = [ex.submit(task, name, iso2) for name, iso2 in countries_en]
        for f in as_completed(futures):
            country_name, iso2, available, error = f.result()
            rows.append({
                "store": "App Store",
                "country": country_name,
                "iso2": iso2,
                "available": available,
                "error": error,
                "store_url": build_apple_store_url(app_id, iso2, l_param),
            })

    rows.sort(key=lambda r: r["country"])
    return rows


# ---------------- TELEGRAM AVAILABILITY BOT ----------------

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
]

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


class BotConfigError(RuntimeError):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def decode_service_account_info() -> dict:
    raw_json = (
        os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        or os.environ.get("GOOGLE_SERVICE_ACCOUNT_INFO")
        or ""
    ).strip()
    if raw_json:
        if raw_json.startswith("{"):
            return json.loads(raw_json)
        return json.loads(base64.b64decode(raw_json).decode("utf-8"))

    path = (
        os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        or ""
    ).strip()
    if path:
        with open(os.path.expanduser(path), "r", encoding="utf-8") as f:
            return json.load(f)

    raise BotConfigError("GOOGLE_SERVICE_ACCOUNT_JSON або GOOGLE_SERVICE_ACCOUNT_FILE не задано.")


def sheets_range(sheet_name: str, cell_range: str) -> str:
    safe_name = (sheet_name or "").replace("'", "''")
    return f"'{safe_name}'!{cell_range}"


def boolish(value, default: bool = True) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    return text not in {"0", "false", "no", "ні", "off", "disabled", "pause", "paused"}


def split_country_codes(value) -> set[str]:
    if isinstance(value, list):
        parts = value
    else:
        parts = re.split(r"[,;\s]+", str(value or ""))
    return {
        str(part).strip().upper()
        for part in parts
        if re.fullmatch(r"[A-Za-z]{2}", str(part).strip())
    }


def join_country_codes(codes) -> str:
    return ",".join(sorted({str(code).strip().upper() for code in codes if str(code).strip()}))


def country_flag(iso2: str) -> str:
    code = (iso2 or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(127397 + ord(char)) for char in code)


def country_label(iso2: str) -> str:
    code = (iso2 or "").strip().upper()
    name, _hl = get_country_meta_by_iso2(code)
    flag = country_flag(code)
    return f"{flag} {name} ({code})".strip()


class GoogleSheetsAvailabilityStore:
    def __init__(
        self,
        spreadsheet_id: str = AVAILABILITY_DB_SPREADSHEET_ID,
        apps_sheet: str = AVAILABILITY_DB_APPS_SHEET,
        log_sheet: str = AVAILABILITY_DB_LOG_SHEET,
    ):
        self.spreadsheet_id = (spreadsheet_id or "").strip()
        self.apps_sheet = apps_sheet
        self.log_sheet = log_sheet
        self._credentials = None

    def _require_config(self):
        if not self.spreadsheet_id:
            raise BotConfigError("AVAILABILITY_DB_SPREADSHEET_ID / GOOGLE_SHEETS_SPREADSHEET_ID не задано.")
        if service_account is None or GoogleAuthRequest is None:
            raise BotConfigError("google-auth не встановлено. Запусти pip install -r requirements.txt.")

    def _token(self) -> str:
        self._require_config()
        if self._credentials is None:
            info = decode_service_account_info()
            self._credentials = service_account.Credentials.from_service_account_info(
                info,
                scopes=list(GOOGLE_SHEETS_SCOPES),
            )
        if not self._credentials.valid:
            self._credentials.refresh(GoogleAuthRequest())
        return self._credentials.token

    def _request(self, method: str, path: str, **kwargs):
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}{path}"
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._token()}"
        headers.setdefault("Accept", "application/json")
        last_error = None
        for attempt in range(1, HTTP_REQUEST_RETRIES + 1):
            try:
                response = session.request(method, url, headers=headers, timeout=30, **kwargs)
            except requests.RequestException as e:
                last_error = e
                if attempt >= HTTP_REQUEST_RETRIES:
                    raise BotConfigError(f"Google Sheets request failed: {e}")
                time.sleep(0.7 * attempt)
                continue

            if response.status_code in {429, 500, 502, 503, 504} and attempt < HTTP_REQUEST_RETRIES:
                time.sleep(0.7 * attempt)
                continue
            break
        else:
            raise BotConfigError(f"Google Sheets request failed: {last_error}")

        if response.status_code >= 400:
            raise BotConfigError(f"Google Sheets HTTP {response.status_code}: {response.text[:500]}")
        if response.text:
            return response.json()
        return {}

    def _values_path(self, range_name: str, suffix: str = "") -> str:
        encoded_range = quote(range_name, safe="!:'")
        return f"/values/{encoded_range}{suffix}"

    def get_sheet_titles(self) -> set[str]:
        data = self._request("GET", "?fields=sheets.properties.title")
        return {
            (((item or {}).get("properties") or {}).get("title") or "")
            for item in data.get("sheets", [])
        }

    def add_sheet(self, title: str):
        self._request("POST", ":batchUpdate", json={
            "requests": [{"addSheet": {"properties": {"title": title}}}]
        })

    def get_values(self, sheet_name: str, cell_range: str) -> list[list]:
        data = self._request("GET", self._values_path(sheets_range(sheet_name, cell_range)))
        return data.get("values") or []

    def update_values(self, sheet_name: str, cell_range: str, values: list[list]):
        self._request(
            "PUT",
            self._values_path(sheets_range(sheet_name, cell_range), "?valueInputOption=RAW"),
            json={"values": values},
        )

    def append_values(self, sheet_name: str, values: list[list]):
        self._request(
            "POST",
            self._values_path(
                sheets_range(sheet_name, "A:Z"),
                ":append?valueInputOption=RAW&insertDataOption=INSERT_ROWS",
            ),
            json={"values": values},
        )

    def ensure_ready(self):
        titles = self.get_sheet_titles()
        if self.apps_sheet not in titles:
            self.add_sheet(self.apps_sheet)
        if self.log_sheet not in titles:
            self.add_sheet(self.log_sheet)

        app_values = self.get_values(self.apps_sheet, "A1:M1")
        if not app_values or [str(v).strip() for v in app_values[0]] != APPS_SHEET_HEADERS:
            self.update_values(self.apps_sheet, "A1:M1", [APPS_SHEET_HEADERS])

        log_values = self.get_values(self.log_sheet, "A1:H1")
        if not log_values or [str(v).strip() for v in log_values[0]] != CHECKS_SHEET_HEADERS:
            self.update_values(self.log_sheet, "A1:H1", [CHECKS_SHEET_HEADERS])

    def load_apps(self) -> list[dict]:
        self.ensure_ready()
        values = self.get_values(self.apps_sheet, "A2:M")
        apps = []
        for offset, row in enumerate(values, start=2):
            row_dict = {
                header: row[idx] if idx < len(row) else ""
                for idx, header in enumerate(APPS_SHEET_HEADERS)
            }
            app_url = str(row_dict.get("app_url") or "").strip()
            app_id = str(row_dict.get("app_id") or "").strip() or extract_google_play_app_id(app_url)
            if not app_url and not app_id:
                continue
            status = str(row_dict.get("status") or "watch").strip().lower()
            if not boolish(row_dict.get("enabled"), default=True) or status in {"disabled", "paused", "pause", "off", "done"}:
                continue
            if not app_url:
                app_url = build_google_play_url(app_id, GOOGLE_PLAY_DEFAULT_INSTALL_COUNTRY, GOOGLE_PLAY_DEFAULT_INSTALL_LANG)
            if not app_id:
                row_dict["last_error"] = "INVALID_GOOGLE_PLAY_URL"
            row_dict.update({
                "row_index": offset,
                "app_url": app_url,
                "app_id": app_id,
                "status": status or "watch",
            })
            apps.append(row_dict)
        return apps

    def update_app(self, row_index: int, updates: dict):
        values = self.get_values(self.apps_sheet, f"A{row_index}:M{row_index}")
        current_row = values[0] if values else []
        row_dict = {
            header: current_row[idx] if idx < len(current_row) else ""
            for idx, header in enumerate(APPS_SHEET_HEADERS)
        }
        row_dict.update(updates)
        row = [row_dict.get(header, "") for header in APPS_SHEET_HEADERS]
        self.update_values(self.apps_sheet, f"A{row_index}:M{row_index}", [row])

    def append_log(self, event: str, app: dict, countries: list[str] | set[str], details: str = ""):
        codes = sorted({str(code).strip().upper() for code in countries if str(code).strip()})
        self.append_values(self.log_sheet, [[
            utc_now_iso(),
            event,
            app.get("app_id") or "",
            app.get("app_name") or "",
            app.get("app_url") or "",
            len(codes),
            join_country_codes(codes),
            details,
        ]])


def safe_int(value, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).replace(" ", "").replace(",", ".")))
    except Exception:
        return default


def country_chip_payload(iso2: str) -> dict:
    code = str(iso2 or "").strip().upper()
    name, _hl = get_country_meta_by_iso2(code)
    return {
        "iso2": code,
        "name": name,
        "flag": country_flag(code),
        "label": country_label(code),
    }


def availability_app_payload(app: dict) -> dict:
    app_id = str(app.get("app_id") or "").strip()
    app_url = str(app.get("app_url") or "").strip()
    status = str(app.get("status") or "watch").strip().lower() or "watch"
    open_codes = split_country_codes(app.get("last_open_countries"))
    closed_codes = split_country_codes(app.get("last_closed_countries"))
    saved_closed_count = safe_int(app.get("last_closed_count"), len(closed_codes))
    closed_count = max(len(closed_codes), saved_closed_count)
    is_live = status == "live" or bool(open_codes)

    return {
        "enabled": boolish(app.get("enabled"), default=True),
        "status": status,
        "is_live": is_live,
        "app_url": app_url or (build_google_play_url(app_id, "US", "en") if app_id else ""),
        "app_id": app_id,
        "app_name": str(app.get("app_name") or app_id or "Untitled app").strip(),
        "owner": str(app.get("owner") or "").strip(),
        "notes": str(app.get("notes") or "").strip(),
        "last_checked_at": str(app.get("last_checked_at") or "").strip(),
        "last_live_at": str(app.get("last_live_at") or "").strip(),
        "open_count": len(open_codes),
        "closed_count": closed_count,
        "closed_codes": sorted(closed_codes),
        "closed_countries": [country_chip_payload(code) for code in sorted(closed_codes)],
        "open_codes": sorted(open_codes),
        "last_error": str(app.get("last_error") or "").strip(),
    }


def build_live_apps_database_payload() -> dict:
    store = GoogleSheetsAvailabilityStore()
    apps = [availability_app_payload(app) for app in store.load_apps()]
    apps.sort(key=lambda item: (
        0 if item["is_live"] else 1,
        -item["closed_count"],
        item["app_name"].lower(),
        item["app_id"].lower(),
    ))

    live_apps = [item for item in apps if item["is_live"]]
    watch_apps = [item for item in apps if not item["is_live"]]
    apps_with_closed = [item for item in apps if item["closed_count"] > 0]
    unique_closed_codes = sorted({
        code
        for item in apps
        for code in item.get("closed_codes", [])
    })

    return {
        "ok": True,
        "source": "Google Sheets",
        "checked_at": utc_now_iso(),
        "apps": apps,
        "stats": {
            "total": len(apps),
            "live": len(live_apps),
            "watch": len(watch_apps),
            "with_closed": len(apps_with_closed),
            "unique_closed_geo": len(unique_closed_codes),
        },
        "unique_closed_countries": [country_chip_payload(code) for code in unique_closed_codes],
    }


def availability_error_is_transient(error: str | None) -> bool:
    return not google_availability_is_closed_error(error)


def summarize_google_availability(app_id: str) -> dict:
    countries_en = get_geo_countries_en()
    open_codes: set[str] = set()
    closed_codes: set[str] = set()
    transient_codes: set[str] = set()
    max_workers = max(1, min(MAX_WORKERS_BOT_AVAILABILITY, len(countries_en) or 1))

    def task(country_name: str, iso2: str):
        available, error = fetch_google_play_availability_confirmed(app_id, iso2, primary_hl="en")
        return iso2, available, error

    def consume_result(iso2: str, available: bool | None, error: str | None):
        if available is True:
            open_codes.add(iso2)
        elif available is False and google_availability_is_closed_error(error):
            closed_codes.add(iso2)
        else:
            transient_codes.add(iso2)

    # Keep only a small window of futures alive. The Telegram worker runs on
    # small Render instances, so the bot path avoids building the full UI rows
    # payload and avoids submitting every country request at once.
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        country_iter = iter(countries_en)
        pending = set()

        def submit_next() -> bool:
            try:
                country_name, iso2 = next(country_iter)
            except StopIteration:
                return False
            pending.add(ex.submit(task, country_name, iso2))
            return True

        for _ in range(max_workers * 2):
            if not submit_next():
                break

        while pending:
            for future in as_completed(pending):
                pending.remove(future)
                consume_result(*future.result())
                submit_next()
                break

    return {
        "total": len(countries_en),
        "open_codes": sorted(open_codes),
        "closed_codes": sorted(closed_codes),
        "transient_codes": sorted(transient_codes),
        "is_live": bool(open_codes),
    }


def format_country_lines(codes: list[str] | set[str], max_items: int = 80) -> str:
    sorted_codes = sorted({str(code).upper() for code in codes})
    labels = [country_label(code) for code in sorted_codes[:max_items]]
    extra = len(sorted_codes) - len(labels)
    text = ", ".join(labels) if labels else "немає"
    if extra > 0:
        text += f" та ще {extra}"
    return text


def telegram_chunk_text(text: str, max_len: int = 3900) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    current = ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}".strip() if current else line
        if len(candidate) > max_len and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def load_card_font(size: int, bold: bool = False):
    if ImageFont is None:
        return None
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
    ]
    for path in candidates:
        try:
            if path and os.path.exists(path):
                return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def fit_text(draw, text: str, font, max_width: int, min_size: int, start_size: int, bold: bool = False):
    value = str(text or "").strip() or "Untitled app"
    font_obj = font
    size = start_size
    while font_obj and size > min_size and draw.textbbox((0, 0), value, font=font_obj)[2] > max_width:
        size -= 4
        font_obj = load_card_font(size, bold=bold)
    if not font_obj:
        return value, font
    while draw.textbbox((0, 0), value, font=font_obj)[2] > max_width and len(value) > 8:
        value = value[:-2].rstrip() + "…"
    return value, font_obj


def text_bbox_size(draw, text: str, font) -> tuple[int, int]:
    if not font:
        return (0, 0)
    bbox = draw.textbbox((0, 0), str(text or ""), font=font)
    return (bbox[2] - bbox[0], bbox[3] - bbox[1])


def wrap_card_text(draw, text: str, font, max_width: int, max_lines: int = 2) -> list[str]:
    value = re.sub(r"\s+", " ", str(text or "").strip()) or "Untitled app"
    words = value.split(" ")
    lines: list[str] = []
    current = ""

    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or text_bbox_size(draw, candidate, font)[0] <= max_width:
            current = candidate
            continue
        lines.append(current)
        current = word
        if len(lines) >= max_lines:
            break

    if current and len(lines) < max_lines:
        lines.append(current)

    if len(lines) > max_lines:
        lines = lines[:max_lines]

    if len(lines) == max_lines:
        line = lines[-1]
        while text_bbox_size(draw, line, font)[0] > max_width and len(line) > 8:
            line = line[:-2].rstrip() + "…"
        lines[-1] = line

    return lines or [value]


def fit_wrapped_card_text(
    draw,
    text: str,
    max_width: int,
    max_lines: int,
    start_size: int,
    min_size: int,
    bold: bool = False,
):
    size = start_size
    font = load_card_font(size, bold=bold)
    while font and size > min_size:
        lines = wrap_card_text(draw, text, font, max_width, max_lines=max_lines)
        if all(text_bbox_size(draw, line, font)[0] <= max_width for line in lines):
            return lines, font
        size -= 4
        font = load_card_font(size, bold=bold)
    return wrap_card_text(draw, text, font, max_width, max_lines=max_lines), font


def draw_shadowed_text(draw, xy: tuple[int, int], text: str, font, fill=(255, 255, 255), shadow=(0, 0, 0, 165)):
    x, y = xy
    draw.text((x + 3, y + 4), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)


def normalize_content_rating_label(value: str) -> str:
    text = str(value or "").strip()
    if not text or text == "—":
        return "—"
    match = re.search(r"(\d{1,2})\s*\+", text)
    if match:
        return f"{match.group(1)}+"
    match = re.search(r"rated\s+for\s+(\d{1,2})", text, flags=re.I)
    if match:
        return f"{match.group(1)}+"
    return text


def extract_card_media_urls(value) -> list[str]:
    urls: list[str] = []
    if isinstance(value, str):
        return [value] if value.startswith(("http://", "https://")) else []
    if isinstance(value, list):
        for item in value:
            urls.extend(extract_card_media_urls(item))
        return urls
    if isinstance(value, dict):
        for key in ("url", "image_url", "src", "original", "original_url", "large", "medium"):
            found = value.get(key)
            if isinstance(found, str) and found.startswith(("http://", "https://")):
                urls.append(found)
        for key in ("screenshots", "android", "images"):
            if key in value:
                urls.extend(extract_card_media_urls(value.get(key)))
    seen = set()
    unique = []
    for url in urls:
        if url not in seen:
            unique.append(url)
            seen.add(url)
    return unique


def sensor_tower_app_card_meta(app: dict) -> dict:
    app_id = str(app.get("app_id") or "").strip()
    fallback_name = str(app.get("app_name") or app_id or "Untitled app").strip()
    meta = {
        "app_id": app_id,
        "name": fallback_name,
        "category": "—",
        "content_rating": "—",
        "icon_url": "",
        "screenshots": [],
    }
    if not app_id:
        return meta

    data, _error = fetch_sensor_tower_app_overview(app_id, "US")
    if not isinstance(data, dict):
        return meta

    categories = [
        format_sensor_tower_category(item.get("name") or item.get("id"))
        for item in (data.get("categories") or [])
        if isinstance(item, dict) and (item.get("name") or item.get("id"))
    ]
    categories = [item for item in categories if item and item != "—"]
    screenshots = data.get("screenshots") or {}
    android_screenshots = screenshots.get("android") if isinstance(screenshots, dict) else screenshots

    meta.update({
        "name": data.get("name") or fallback_name,
        "category": categories[0] if categories else "—",
        "content_rating": normalize_content_rating_label(data.get("content_rating") or "—"),
        "icon_url": data.get("icon_url") or "",
        "screenshots": extract_card_media_urls(android_screenshots)[:3],
    })
    return meta


def rounded_image(image, radius: int):
    if Image is None or ImageOps is None:
        return image
    img = image.convert("RGBA")
    mask = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, img.size[0], img.size[1]), radius=radius, fill=255)
    img.putalpha(mask)
    return img


def fetch_card_icon(icon_url: str, size: int):
    if Image is None or not icon_url:
        return None
    try:
        response = session.get(icon_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if response.status_code != 200:
            return None
        icon = Image.open(io.BytesIO(response.content)).convert("RGBA")
        icon = ImageOps.fit(icon, (size, size), method=Image.Resampling.LANCZOS)
        return rounded_image(icon, 28)
    except Exception:
        return None


def fetch_card_media_image(url: str, size: tuple[int, int], radius: int):
    if Image is None or ImageOps is None or not url:
        return None
    try:
        response = session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if response.status_code != 200:
            return None
        image = Image.open(io.BytesIO(response.content)).convert("RGBA")
        image = ImageOps.fit(image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        return rounded_image(image, radius)
    except Exception:
        return None


def draw_card_pill(draw, xy: tuple[int, int], label: str, font, fill, outline=None, text_fill=(255, 255, 255), min_width=0, pad_x=28, pad_y=12):
    x, y = xy
    text_w, text_h = text_bbox_size(draw, label, font)
    width = max(min_width, text_w + pad_x * 2)
    height = text_h + pad_y * 2
    shadow_offset = 8
    draw.rounded_rectangle(
        (x + shadow_offset, y + shadow_offset, x + width + shadow_offset, y + height + shadow_offset),
        radius=max(12, height // 3),
        fill=(0, 0, 0, 80),
    )
    draw.rounded_rectangle(
        (x, y, x + width, y + height),
        radius=max(12, height // 3),
        fill=fill,
        outline=outline,
        width=2 if outline else 1,
    )
    draw.text((x + pad_x, y + pad_y - 2), label, font=font, fill=text_fill)
    return width, height


def build_telegram_app_card(app: dict) -> bytes | None:
    if not TELEGRAM_SEND_APP_CARD or Image is None or ImageDraw is None:
        return None

    app_id = str(app.get("app_id") or "").strip().lower()
    cache_key = ("telegram_app_card", app_id)
    cached = TELEGRAM_APP_CARD_CACHE.get(cache_key)
    if cached is not CACHE_MISS:
        return cached

    bg_path = resource_path("static/img/telegram-alert-bg.png")
    if not os.path.exists(bg_path):
        return None

    try:
        card = Image.open(bg_path).convert("RGBA")
    except Exception:
        return None

    draw = ImageDraw.Draw(card)
    meta = sensor_tower_app_card_meta(app)
    width, height = card.size

    title_font = load_card_font(74, bold=True)
    meta_font = load_card_font(42, bold=True)
    rating_font = load_card_font(38, bold=True)
    small_font = load_card_font(30, bold=True)

    panel = Image.new("RGBA", card.size, (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rounded_rectangle((44, 58, width - 42, 1018), radius=34, fill=(0, 0, 0, 82))
    panel_draw.rounded_rectangle((44, 58, width - 42, 1018), radius=34, outline=(255, 255, 255, 34), width=2)
    panel_draw.rounded_rectangle((54, 68, 1008, 482), radius=30, fill=(0, 0, 0, 88))
    card = Image.alpha_composite(card, panel)
    draw = ImageDraw.Draw(card)

    icon_size = 300
    icon_x, icon_y = 78, 94
    icon = fetch_card_icon(meta.get("icon_url"), icon_size)
    if icon is not None:
        shadow = Image.new("RGBA", (icon_size + 22, icon_size + 22), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_draw.rounded_rectangle((11, 11, icon_size + 11, icon_size + 11), radius=42, fill=(0, 0, 0, 150))
        card.paste(shadow, (icon_x - 11, icon_y - 4), shadow)
        card.paste(icon, (icon_x, icon_y), icon)
    else:
        draw.rounded_rectangle(
            (icon_x, icon_y, icon_x + icon_size, icon_y + icon_size),
            radius=42,
            fill=(22, 28, 36),
            outline=(255, 255, 255),
            width=3,
        )
        initials = (meta.get("name") or "APP")[:2].upper()
        initials_font = load_card_font(74, bold=True)
        bbox = draw.textbbox((0, 0), initials, font=initials_font)
        draw.text(
            (icon_x + (icon_size - (bbox[2] - bbox[0])) / 2, icon_y + (icon_size - (bbox[3] - bbox[1])) / 2 - 8),
            initials,
            fill=(255, 255, 255),
            font=initials_font,
        )

    text_x = 420
    title_lines, title_font = fit_wrapped_card_text(draw, meta.get("name"), 560, 2, 74, 50, bold=True)
    title_y = 100
    title_line_height = max(70, text_bbox_size(draw, "Ag", title_font)[1] + 18)
    line_gap = 14
    for index, line in enumerate(title_lines):
        draw_shadowed_text(draw, (text_x, title_y + index * title_line_height), line, title_font)

    after_title_y = title_y + len(title_lines) * title_line_height + line_gap
    category = meta.get("category") or "—"
    category_text, meta_font = fit_text(draw, f"Android  •  {category}", meta_font, 560, 30, 42, bold=True)
    draw_shadowed_text(draw, (text_x, after_title_y), category_text, meta_font)

    rating_label = normalize_content_rating_label(meta.get("content_rating"))
    badge_y = after_title_y + 72
    draw_card_pill(
        draw,
        (text_x, badge_y),
        rating_label,
        font=rating_font,
        fill=(230, 0, 0, 238),
        outline=(255, 52, 52, 215),
        min_width=150,
        pad_x=34,
        pad_y=14,
    )

    if app_id:
        package_text, small_font = fit_text(draw, app_id, small_font, 500, 23, 30, bold=True)
        package_y = badge_y + 88
        draw_card_pill(
            draw,
            (text_x, package_y),
            package_text,
            font=small_font,
            fill=(6, 12, 20, 170),
            outline=(255, 255, 255, 42),
            text_fill=(245, 245, 245),
            pad_x=24,
            pad_y=11,
        )

    screenshots = meta.get("screenshots") or []
    media_y = 505
    media_h = 440
    media_gap = 28
    media_w = (width - 120 - media_gap * 2) // 3
    rendered_media = 0
    for index, url in enumerate(screenshots[:3]):
        image = fetch_card_media_image(url, (media_w, media_h), 26)
        if image is None:
            continue
        x = 60 + index * (media_w + media_gap)
        media_shadow = Image.new("RGBA", (media_w + 20, media_h + 20), (0, 0, 0, 0))
        media_shadow_draw = ImageDraw.Draw(media_shadow)
        media_shadow_draw.rounded_rectangle((10, 10, media_w + 10, media_h + 10), radius=30, fill=(0, 0, 0, 130))
        card.paste(media_shadow, (x - 10, media_y - 4), media_shadow)
        card.paste(image, (x, media_y), image)
        draw.rounded_rectangle(
            (x, media_y, x + media_w, media_y + media_h),
            radius=26,
            outline=(255, 255, 255, 54),
            width=3,
        )
        rendered_media += 1

    if rendered_media == 0:
        fallback_font = load_card_font(48, bold=True)
        fallback_small_font = load_card_font(34, bold=True)
        draw_card_pill(
            draw,
            (72, 530),
            "WWA ASO Availability",
            fallback_font,
            fill=(8, 18, 30, 186),
            outline=(255, 255, 255, 50),
            min_width=520,
            pad_x=34,
            pad_y=22,
        )
        draw_shadowed_text(
            draw,
            (86, 650),
            "Live app monitoring • Google Play GEO checks",
            fallback_small_font,
            fill=(245, 245, 245),
        )

    output = io.BytesIO()
    card.convert("RGB").save(output, format="PNG", optimize=True)
    data = output.getvalue()
    TELEGRAM_APP_CARD_CACHE.set(cache_key, data)
    return data


def send_telegram_message(text: str):
    if not TELEGRAM_BOT_TOKEN:
        raise BotConfigError("TELEGRAM_BOT_TOKEN не задано.")
    if not TELEGRAM_CHAT_ID:
        raise BotConfigError("TELEGRAM_CHAT_ID не задано.")

    sent = []
    for chunk in telegram_chunk_text(text):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        last_error = None
        for attempt in range(1, HTTP_REQUEST_RETRIES + 1):
            try:
                response = session.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json=payload,
                    timeout=20,
                )
            except requests.RequestException as e:
                last_error = e
                if attempt >= HTTP_REQUEST_RETRIES:
                    raise BotConfigError(f"Telegram request failed: {e}")
                time.sleep(0.7 * attempt)
                continue

            if response.status_code in {429, 500, 502, 503, 504} and attempt < HTTP_REQUEST_RETRIES:
                time.sleep(0.7 * attempt)
                continue
            break
        else:
            raise BotConfigError(f"Telegram request failed: {last_error}")

        if response.status_code >= 400:
            raise BotConfigError(f"Telegram HTTP {response.status_code}: {response.text[:500]}")
        sent.append(response.json())
    return sent


def send_telegram_photo(photo_bytes: bytes, caption: str = ""):
    if not TELEGRAM_BOT_TOKEN:
        raise BotConfigError("TELEGRAM_BOT_TOKEN не задано.")
    if not TELEGRAM_CHAT_ID:
        raise BotConfigError("TELEGRAM_CHAT_ID не задано.")

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    if caption:
        payload["caption"] = caption

    last_error = None
    response = None
    for attempt in range(1, HTTP_REQUEST_RETRIES + 1):
        try:
            files = {"photo": ("wwa-app-card.png", io.BytesIO(photo_bytes), "image/png")}
            response = session.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                data=payload,
                files=files,
                timeout=35,
            )
        except requests.RequestException as e:
            last_error = e
            if attempt >= HTTP_REQUEST_RETRIES:
                raise BotConfigError(f"Telegram photo request failed: {e}")
            time.sleep(0.7 * attempt)
            continue

        if response.status_code in {429, 500, 502, 503, 504} and attempt < HTTP_REQUEST_RETRIES:
            time.sleep(0.7 * attempt)
            continue
        break
    else:
        raise BotConfigError(f"Telegram photo request failed: {last_error}")

    if response is None or response.status_code >= 400:
        body = response.text[:500] if response is not None else str(last_error)
        status = response.status_code if response is not None else "unknown"
        raise BotConfigError(f"Telegram photo HTTP {status}: {body}")
    return [response.json()]


def send_telegram_event_message(text: str, app: dict):
    try:
        card = build_telegram_app_card(app)
    except Exception:
        card = None

    if not card:
        return send_telegram_message(text)

    # Telegram photo captions are limited to 1024 characters. For long country
    # lists, send the visual card first and the full text immediately after.
    caption = text if len(text) <= 950 else ""
    try:
        sent = send_telegram_photo(card, caption=caption)
        if not caption:
            sent.extend(send_telegram_message(text))
        return sent
    except Exception:
        return send_telegram_message(text)


def build_bot_message(event: str, app: dict, snapshot: dict, changed_codes: list[str] | set[str]) -> str:
    app_id = app.get("app_id") or ""
    app_name = app.get("app_name") or app_id
    app_url = app.get("app_url") or build_google_play_url(app_id, "US", "en")
    escaped_name = html_lib.escape(app_name)
    escaped_id = html_lib.escape(app_id)
    escaped_url = html_lib.escape(app_url)
    open_count = len(snapshot.get("open_codes") or [])
    closed_count = len(snapshot.get("closed_codes") or [])
    total = snapshot.get("total") or 0

    if event == "new_live":
        title = "🟢 Новий додаток вийшов у Live"
        country_intro = "Закриті країни зараз"
    elif event == "new_opened":
        title = "🔵 Availability змінився: країни відкрилися"
        country_intro = "Нові відкриті країни"
    else:
        title = "🔴 Availability змінився: країни закрилися"
        country_intro = "Нові закриті країни"

    return "\n".join([
        f"<b>{title}</b>",
        "",
        f"<b>{escaped_name}</b>",
        f"<code>{escaped_id}</code>",
        f'<a href="{escaped_url}">Google Play</a>',
        "",
        f"Open: <b>{open_count}</b> / {total}",
        f"Closed: <b>{closed_count}</b>",
        "",
        f"<b>{country_intro}:</b>",
        html_lib.escape(format_country_lines(changed_codes)),
    ])


def run_availability_bot_check(
    send_messages: bool = True,
    limit: int = AVAILABILITY_CHECK_LIMIT,
    write_changes: bool = True,
    progress_callback=None,
) -> dict:
    store = GoogleSheetsAvailabilityStore()
    apps = store.load_apps()
    apps_to_check = apps[:limit]
    now = utc_now_iso()
    result = {
        "ok": True,
        "checked_at": now,
        "apps_total": len(apps),
        "apps_checked": 0,
        "notifications": [],
        "skipped": [],
        "errors": [],
    }

    def emit_progress(app_index: int, app_id: str):
        if not progress_callback:
            return
        try:
            progress_callback({
                "app_index": app_index,
                "apps_to_check": len(apps_to_check),
                "app_id": app_id,
                "apps_checked": result["apps_checked"],
                "errors": len(result["errors"]),
                "notifications": len(result["notifications"]),
                "skipped": len(result["skipped"]),
            })
        except Exception:
            pass

    for app_index, app in enumerate(apps_to_check, start=1):
        app_id = app.get("app_id") or ""
        if not app_id:
            result["skipped"].append({"row": app.get("row_index"), "reason": "missing_app_id"})
            emit_progress(app_index, "")
            continue

        try:
            snapshot = summarize_google_availability(app_id)
        except Exception as e:
            error = f"AVAILABILITY_CHECK_ERROR:{e}"
            if write_changes:
                store.update_app(app["row_index"], {"last_checked_at": now, "last_error": error})
            result["errors"].append({"app_id": app_id, "error": error})
            emit_progress(app_index, app_id)
            continue

        result["apps_checked"] += 1
        transient_count = len(snapshot["transient_codes"])
        if transient_count > max(8, int((snapshot["total"] or 1) * 0.35)):
            error = f"TOO_MANY_TRANSIENT_ERRORS:{transient_count}"
            if write_changes:
                store.update_app(app["row_index"], {"last_checked_at": now, "last_error": error})
            result["errors"].append({"app_id": app_id, "error": error})
            emit_progress(app_index, app_id)
            continue

        prev_open = split_country_codes(app.get("last_open_countries"))
        prev_closed = split_country_codes(app.get("last_closed_countries"))
        prev_status = str(app.get("status") or "").strip().lower()
        prev_live = prev_status == "live" or bool(prev_open)
        current_open = set(snapshot["open_codes"])
        current_closed = set(snapshot["closed_codes"])
        current_live = bool(current_open)

        events: list[tuple[str, set[str]]] = []
        if current_live and not prev_live:
            events.append(("new_live", current_closed))
        elif current_live and prev_live:
            newly_closed = prev_open & current_closed
            newly_opened = prev_closed & current_open
            if newly_closed:
                events.append(("new_closed", newly_closed))
            if newly_opened:
                events.append(("new_opened", newly_opened))

        update_payload = {
            "enabled": app.get("enabled") if str(app.get("enabled") or "").strip() else "TRUE",
            "status": "live" if current_live else "watch",
            "app_url": app.get("app_url") or build_google_play_url(app_id, "US", "en"),
            "app_id": app_id,
            "app_name": app.get("app_name") or app_id,
            "owner": app.get("owner") or "",
            "notes": app.get("notes") or "",
            "last_checked_at": now,
            "last_live_at": app.get("last_live_at") or (now if current_live else ""),
            "last_open_countries": join_country_codes(current_open),
            "last_closed_countries": join_country_codes(current_closed),
            "last_closed_count": len(current_closed),
            "last_error": "",
        }
        if write_changes:
            store.update_app(app["row_index"], update_payload)

        for event, changed_codes in events:
            app_for_message = {**app, **update_payload}
            message = build_bot_message(event, app_for_message, snapshot, changed_codes)
            if send_messages:
                send_telegram_event_message(message, app_for_message)
            if write_changes:
                store.append_log(event, app_for_message, changed_codes, f"open={len(current_open)} closed={len(current_closed)}")
            result["notifications"].append({
                "event": event,
                "app_id": app_id,
                "countries_count": len(changed_codes),
                "countries": sorted(changed_codes),
            })

        emit_progress(app_index, app_id)

    return result


def task_request_authorized() -> bool:
    if not AVAILABILITY_TASK_SECRET:
        return is_local_request()
    provided = (
        request.headers.get("X-Task-Secret")
        or request.args.get("secret")
        or ((request.json or {}).get("secret") if request.is_json else "")
        or ""
    ).strip()
    return provided == AVAILABILITY_TASK_SECRET


# ---------------- NEW: GEO LINK HELPERS ----------------

def resolve_country_for_geo_link(raw_country: str) -> tuple[str, str] | None:
    """
    Returns (ISO2, HL) for Google Play gl/hl.
    Accepts:
      - ISO2 (e.g. UA, US, DE)
      - English country name (full or partial)
      - Some UA/RU aliases (e.g. "україна", "сша")
    """
    if not raw_country:
        return None

    c_norm = raw_country.strip()
    c_key = c_norm.lower()

    aliases = {
        "україна": "Ukraine",
        "украина": "Ukraine",
        "росія": "Russia",
        "россия": "Russia",
        "сша": "United States",
        "сполучені штати": "United States",
        "сполученi штати": "United States",
        "великобританія": "United Kingdom",
        "велика британія": "United Kingdom",
        "туреччина": "Turkey",
        "польща": "Poland",
        "німеччина": "Germany",
        "германія": "Germany",
        "франція": "France",
        "іспанія": "Spain",
        "італія": "Italy",
        "канада": "Canada",
        "бразилія": "Brazil",
        "японія": "Japan",
        "індія": "India",
        "китай": "China",
        "південна корея": "South Korea",
        "корея": "South Korea",
    }

    # ISO2 directly
    if re.fullmatch(r"[A-Za-z]{2}", c_norm):
        iso2 = c_norm.upper()
        for _, (cc, hl) in COUNTRIES_FULL.items():
            if cc.upper() == iso2:
                return iso2, hl
        return iso2, "en"

    # alias -> english name
    name = aliases.get(c_key)

    # exact match (case-insensitive)
    if not name:
        for n in COUNTRIES_FULL.keys():
            if n.lower() == c_key:
                name = n
                break

    # partial match (best-effort)
    if not name:
        for n in COUNTRIES_FULL.keys():
            if c_key in n.lower():
                name = n
                break

    if not name or name not in COUNTRIES_FULL:
        return None

    iso2, hl = COUNTRIES_FULL[name]
    return iso2, hl


# ---------------- ROUTES (PAGES) ----------------

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/availability")
def availability_page():
    return render_template("availability.html")


@app.get("/geo-link")
def geo_link_page():
    # for frontend autocomplete
    countries = {name: {"gl": iso2, "hl": hl} for name, (iso2, hl) in COUNTRIES_FULL.items()}
    return render_template("geo_link.html", countries_json=json.dumps(countries, ensure_ascii=False))


@app.get("/app-overview")
def app_overview_page():
    countries = [
        {"name": name, "code": iso2}
        for name, (iso2, _hl) in sorted(COUNTRIES_FULL.items(), key=lambda item: item[0])
    ]
    return render_template("app_overview.html", countries=countries)


@app.get("/live-apps")
def live_apps_page():
    return render_template("live_apps.html")


@app.get("/api/live-apps")
def live_apps_api():
    try:
        return jsonify(build_live_apps_database_payload())
    except BotConfigError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"LIVE_APPS_DATABASE_ERROR:{e}"}), 500


# ---------------- API: APP OVERVIEW ----------------

@app.post("/app-overview/lookup")
def app_overview_lookup():
    payload = request.json or {}
    app_id = normalize_android_package_input(payload.get("app_id") or payload.get("query") or payload.get("url") or "")
    country = normalize_sensor_tower_country(payload.get("country") or "US")

    if not app_id:
        return jsonify({"error": "Введи bundle/package name, наприклад com.dragonplus.cookingfr."}), 400

    data, error = build_app_overview_payload(app_id, country)
    if error:
        return jsonify({
            "error": f"Sensor Tower не віддав дані для {app_id}. Причина: {error}",
            "app_id": app_id,
            "country": country,
            "sensor_tower_url": build_sensor_tower_overview_url(app_id, country),
        }), 502

    return jsonify(data)


@app.post("/app-overview/publisher")
def app_overview_publisher_lookup():
    payload = request.json or {}
    publisher_id = normalize_sensor_tower_publisher_id(payload.get("publisher_id") or payload.get("publisher") or "")
    country = normalize_sensor_tower_country(payload.get("country") or "US")
    os_name = normalize_sensor_tower_os(payload.get("os") or "android")

    try:
        limit = int(payload.get("limit") or SENSOR_TOWER_PUBLISHER_APPS_LIMIT)
    except Exception:
        limit = SENSOR_TOWER_PUBLISHER_APPS_LIMIT
    try:
        offset = int(payload.get("offset") or 0)
    except Exception:
        offset = 0

    limit = min(max(limit, 1), SENSOR_TOWER_PUBLISHER_APPS_LIMIT)
    offset = max(offset, 0)
    sort_by = payload.get("sort_by") or "downloads"

    if not publisher_id:
        return jsonify({"error": "Sensor Tower publisher id не знайдено."}), 400

    data, error = build_sensor_tower_publisher_payload(
        publisher_id=publisher_id,
        country=country,
        os_name=os_name,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
    )
    if error:
        return jsonify({
            "error": f"Sensor Tower не віддав publisher data. Причина: {error}",
            "publisher_id": publisher_id,
            "country": country,
            "sensor_tower_url": build_sensor_tower_publisher_url(os_name, publisher_id),
        }), 502

    return jsonify(data)


# ---------------- API: GEO LINK ----------------

@app.post("/api/geo-link")
def api_geo_link():
    payload = request.json or {}
    raw_url = (payload.get("url") or "").strip()
    raw_country = (payload.get("country") or "").strip()

    if not raw_url:
        return jsonify({"error": "Встав посилання на Google Play додаток."}), 400
    if not raw_country:
        return jsonify({"error": "Вкажи країну (назва або код, наприклад UA / US / DE)."}), 400

    app_id = extract_google_play_app_id(raw_url)
    if not app_id:
        return jsonify({"error": "Не зміг знайти параметр id у посиланні Google Play (details?id=...)."}), 400

    resolved = resolve_country_for_geo_link(raw_country)
    if not resolved:
        return jsonify({"error": "Країну не знайдено. Спробуй англійську назву (Ukraine, Germany) або код (UA, DE)."}), 400

    iso2, hl = resolved
    url = build_google_play_url(app_id, iso2, hl)

    return jsonify({
        "app_id": app_id,
        "country": raw_country,
        "gl": iso2,
        "hl": hl,
        "url": url,
    })


# ---------------- EXISTING API: /check ----------------

@app.post("/check")
def check():
    payload = request.json or {}
    url = (payload.get("url") or "").strip()
    mode = (payload.get("mode") or "toolbox").strip().lower()
    request_appmagic_token = normalize_appmagic_token(
        payload.get("appmagic_token")
        or payload.get("appmagic_bearer_token")
        or request.headers.get("X-AppMagic-Token")
        or ""
    )
    if request_appmagic_token:
        g.appmagic_token_override = request_appmagic_token

    try:
        threshold = float(payload.get("threshold", DEFAULT_THRESHOLD))
    except Exception:
        threshold = DEFAULT_THRESHOLD

    threshold = max(0.0, min(5.0, threshold))

    store = detect_store(url)
    if store == "unknown":
        return jsonify({"error": "Only Google Play, App Magic Google Play, or Apple App Store links are supported."}), 400

    if store in ("google_play", "appmagic_google_play"):
        app_id = extract_google_play_app_id(url) if store == "google_play" else extract_appmagic_google_play_app_id(url)
        if not app_id:
            return jsonify({"error": "Invalid Google Play/App Magic link (missing package id)."}), 400

        if mode == "appmagic":
            try:
                all_rows, below_rows, appmagic_meta = check_google_appmagic(app_id, threshold)
            except ValueError as e:
                return jsonify({"error": f"App Magic error: {e}"}), 502

            return jsonify({
                "store": "google_play",
                "mode": "appmagic",
                "countries_count": len(all_rows),
                "app_id": app_id,
                "threshold": threshold,
                "below": below_rows,
                "all": all_rows,
                "appmagic": appmagic_meta,
                "app_total_installs": appmagic_meta.get("app_total_installs"),
                "app_total_installs_label": appmagic_meta.get("app_total_installs_label"),
                "app_total_installs_source": appmagic_meta.get("app_total_installs_source"),
                "app_total_installs_source_label": appmagic_meta.get("app_total_installs_source_label"),
            })

        countries = get_countries_by_mode(mode)
        all_rows, below_rows = check_google(app_id, threshold, countries)
        app_total_meta = build_app_total_installs_meta(app_id, prefer_appmagic=False)
        return jsonify({
            "store": "google_play",
            "mode": mode,
            "countries_count": len(countries),
            "app_id": app_id,
            "threshold": threshold,
            "below": below_rows,
            "all": all_rows,
            **app_total_meta,
        })

    if mode == "appmagic":
        return jsonify({"error": "App Magic mode supports only Google Play links."}), 400

    countries = get_countries_by_mode(mode)
    apple_id = extract_apple_app_id(url)
    if not apple_id:
        return jsonify({"error": "Apple app id not found in the link (expected .../id123...)."}), 400

    l_param = extract_apple_lang_param(url)
    all_rows, below_rows = check_apple(apple_id, threshold, l_param, countries)

    return jsonify({
        "store": "apple_app_store",
        "mode": mode,
        "countries_count": len(countries),
        "app_id": apple_id,
        "threshold": threshold,
        "below": below_rows,
        "all": all_rows,
    })


# ---------------- EXISTING API: /availability/check ----------------

@app.post("/availability/check")
def availability_check():
    payload = request.json or {}
    url = (payload.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Please paste the app URL."}), 400

    store = detect_store(url)
    if store == "unknown":
        return jsonify({"error": "Only Google Play or Apple App Store links are supported."}), 400

    geo = get_geo_countries_en()

    if store == "google_play":
        app_id = extract_google_play_app_id(url)
        if not app_id:
            return jsonify({"error": "Invalid Google Play link (missing id=...)"}), 400

        rows = check_availability_google(app_id, geo)
        return jsonify({
            "store": "google_play",
            "app_id": app_id,
            "countries_count": len(geo),
            "rows": rows,
        })

    apple_id = extract_apple_app_id(url)
    if not apple_id:
        return jsonify({"error": "Apple app id not found in the link (expected .../id123...)."}), 400

    l_param = extract_apple_lang_param(url)
    rows = check_availability_apple(apple_id, l_param, geo)
    return jsonify({
        "store": "apple_app_store",
        "app_id": apple_id,
        "countries_count": len(geo),
        "rows": rows,
    })


# ---------------- TASK API: TELEGRAM AVAILABILITY BOT ----------------

@app.route("/tasks/check-availability", methods=["GET", "POST"])
def task_check_availability():
    if not task_request_authorized():
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    payload = request.json if request.is_json else {}
    dry_run_raw = (
        request.args.get("dry_run")
        or request.args.get("dry")
        or (payload or {}).get("dry_run")
        or ""
    )
    dry_run = boolish(dry_run_raw, default=False) if str(dry_run_raw).strip() else False
    try:
        limit = int(request.args.get("limit") or (payload or {}).get("limit") or AVAILABILITY_CHECK_LIMIT)
    except Exception:
        limit = AVAILABILITY_CHECK_LIMIT
    limit = max(1, min(1000, limit))

    try:
        result = run_availability_bot_check(send_messages=not dry_run, limit=limit, write_changes=not dry_run)
    except BotConfigError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"AVAILABILITY_TASK_ERROR:{e}"}), 500

    result["dry_run"] = dry_run
    return jsonify(result)


# ---------------- AUTO OPEN BROWSER ----------------

def open_browser(port: int):
    webbrowser.open(f"http://{HOST}:{port}/")


def run_cli_command() -> int | None:
    if len(sys.argv) < 2 or sys.argv[1] not in {"bot-check", "availability-bot-check"}:
        return None

    dry_run = "--dry-run" in sys.argv or "--dry" in sys.argv
    try:
        result = run_availability_bot_check(send_messages=not dry_run, write_changes=not dry_run)
        result["dry_run"] = dry_run
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    cli_exit_code = run_cli_command()
    if cli_exit_code is not None:
        sys.exit(cli_exit_code)

    runtime_port = ensure_single_instance_or_get_port(PORT)
    _runtime_port = runtime_port

    _save_state(os.getpid(), runtime_port)
    atexit.register(_clear_state)

    threading.Timer(1.0, open_browser, args=(runtime_port,)).start()

    app.run(host=HOST, port=runtime_port, debug=False, use_reloader=False)
