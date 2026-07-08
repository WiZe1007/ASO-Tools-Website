import html
import json
import os
import re
import threading
import time
import traceback
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import requests

# Background workers on Render Starter have 512 MB RAM. Set conservative
# defaults before importing app.py, because app.py reads these env values at
# import time.
os.environ.setdefault("WWA_BOT_MAX_WORKERS_AVAILABILITY", "3")
os.environ.setdefault("WWA_CACHE_MAX_ITEMS", "240")

from app import (
    AVAILABILITY_CHECK_LIMIT,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    BotConfigError,
    GoogleSheetsAvailabilityStore,
    build_app_overview_payload,
    build_google_play_indexing_payload,
    build_google_play_url,
    build_live_apps_database_payload,
    country_label,
    extract_google_play_app_id,
    format_country_lines,
    normalize_android_package_input,
    normalize_indexing_countries,
    normalize_indexing_keywords,
    normalize_indexing_limit,
    resolve_country_for_geo_link,
    run_availability_bot_check,
    session,
    summarize_google_availability,
    telegram_chunk_text,
)


def env_int(name: str, default: int, min_value: int = 1, max_value: int = 3600) -> int:
    try:
        value = int(os.environ.get(name, default))
    except Exception:
        value = default
    return max(min_value, min(max_value, value))


BOT_TIMEZONE = os.environ.get("BOT_TIMEZONE", "Europe/Kiev").strip() or "Europe/Kiev"
BOT_CHECK_HOURS = os.environ.get("BOT_CHECK_HOURS", "9,15,21").strip() or "9,15,21"
BOT_DISPLAY_NAME = os.environ.get("BOT_DISPLAY_NAME", "WWA ASO Availability Bot").strip() or "WWA ASO Availability Bot"
BOT_POLL_TIMEOUT = env_int("BOT_POLL_TIMEOUT", 25, 5, 50)
BOT_DROP_PENDING_UPDATES = os.environ.get("BOT_DROP_PENDING_UPDATES", "1").strip() != "0"
BOT_MAX_COMMAND_CHECKS = env_int("BOT_MAX_COMMAND_CHECKS", AVAILABILITY_CHECK_LIMIT, 1, 1000)
BOT_SCHEDULE_GRACE_MINUTES = env_int("BOT_SCHEDULE_GRACE_MINUTES", 10, 1, 59)
BOT_API_RETRIES = env_int("BOT_API_RETRIES", 4, 1, 8)
BOT_PROGRESS_EVERY_APPS = env_int("BOT_PROGRESS_EVERY_APPS", 10, 1, 100)
TELEGRAM_SEND_EMPTY_SUMMARY = os.environ.get("TELEGRAM_SEND_EMPTY_SUMMARY", "1").strip() != "0"
TELEGRAM_PRIVATE_MENU_OPEN = os.environ.get("TELEGRAM_PRIVATE_MENU_OPEN", "1").strip() != "0"
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
TELEGRAM_LIVE_DB_MENU_LIMIT = env_int("TELEGRAM_LIVE_DB_MENU_LIMIT", 12, 1, 40)

check_lock = threading.Lock()
last_scheduled_key = ""
active_check_state: dict = {}
user_sessions: dict[str, dict] = {}
bot_username_cache = TELEGRAM_BOT_USERNAME


def parse_check_hours() -> set[int]:
    hours = set()
    for part in BOT_CHECK_HOURS.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hour = int(part)
        except Exception:
            continue
        if 0 <= hour <= 23:
            hours.add(hour)
    return hours or {9, 15, 21}


def allowed_chat_ids() -> set[str]:
    raw = ",".join([
        TELEGRAM_CHAT_ID or "",
        os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", ""),
    ])
    return {part.strip() for part in raw.split(",") if part.strip()}


def bot_api(method: str, payload: dict | None = None, timeout: int = 35) -> dict:
    if not TELEGRAM_BOT_TOKEN:
        raise BotConfigError("TELEGRAM_BOT_TOKEN не задано.")

    last_error = None
    for attempt in range(1, BOT_API_RETRIES + 1):
        try:
            response = session.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}",
                json=payload or {},
                timeout=timeout,
            )
        except requests.RequestException as e:
            last_error = e
            if attempt >= BOT_API_RETRIES:
                raise BotConfigError(f"Telegram {method} request failed: {e}")
            time.sleep(0.8 * attempt)
            continue

        if response.status_code in {429, 500, 502, 503, 504} and attempt < BOT_API_RETRIES:
            time.sleep(0.8 * attempt)
            continue
        break
    else:
        raise BotConfigError(f"Telegram {method} request failed: {last_error}")

    if response.status_code >= 400:
        raise BotConfigError(f"Telegram {method} HTTP {response.status_code}: {response.text[:500]}")
    data = response.json()
    if not data.get("ok"):
        raise BotConfigError(f"Telegram {method} error: {data}")
    return data


def send_message(chat_id: str | int, text: str, reply_markup: dict | None = None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return bot_api("sendMessage", payload)


def safe_send_message(chat_id: str | int, text: str, reply_markup: dict | None = None):
    try:
        return send_message(chat_id, text, reply_markup=reply_markup)
    except Exception as e:
        print(json.dumps({
            "ok": False,
            "event": "telegram_send_failed",
            "chat_id": str(chat_id),
            "error": str(e),
        }, ensure_ascii=False))
        return None


def answer_callback_query(callback_query_id: str, text: str | None = None, show_alert: bool = False):
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text[:200]
        payload["show_alert"] = show_alert
    return bot_api("answerCallbackQuery", payload, timeout=10)


def send_long_message(chat_id: str | int, text: str, reply_markup: dict | None = None):
    chunks = telegram_chunk_text(text, max_len=3900)
    result = None
    for idx, chunk in enumerate(chunks):
        result = safe_send_message(chat_id, chunk, reply_markup=reply_markup if idx == len(chunks) - 1 else None)
    return result


def escape(value) -> str:
    return html.escape(str(value or ""))


def event_label(event: str) -> str:
    return {
        "new_live": "новий Live",
        "new_closed": "країни закрилися",
        "new_opened": "країни відкрилися",
    }.get(str(event or ""), str(event or "event"))


def summary_text(result: dict, title: str = "Availability check finished") -> str:
    notifications = result.get("notifications") or []
    errors = result.get("errors") or []
    skipped = result.get("skipped") or []

    lines = [
        f"<b>{escape(title)}</b>",
        "",
        f"Checked apps: <b>{result.get('apps_checked', 0)}</b> / {result.get('apps_total', 0)}",
        f"Notifications: <b>{len(notifications)}</b>",
        f"Errors: <b>{len(errors)}</b>",
        f"Skipped: <b>{len(skipped)}</b>",
    ]

    if notifications:
        lines.extend(["", "<b>Events:</b>"])
        for item in notifications[:20]:
            countries = ", ".join(item.get("countries") or [])
            lines.append(
                f"• {escape(event_label(item.get('event')))}: <code>{escape(item.get('app_id'))}</code> "
                f"({item.get('countries_count', 0)} countries: {escape(countries)})"
            )
        if len(notifications) > 20:
            lines.append(f"…and {len(notifications) - 20} more")

    if errors:
        lines.extend(["", "<b>Errors:</b>"])
        for item in errors[:10]:
            lines.append(f"• <code>{escape(item.get('app_id'))}</code>: {escape(item.get('error'))}")
        if len(errors) > 10:
            lines.append(f"…and {len(errors) - 10} more")

    return "\n".join(lines)


def scheduled_summary_title(result: dict) -> str:
    notifications = result.get("notifications") or []
    errors = result.get("errors") or []
    if errors:
        return "Автоматична перевірка завершена: є помилки"
    if notifications:
        return "Автоматична перевірка завершена: є зміни"
    return "Автоматична перевірка завершена: змін не було"


def help_text(chat_id: str | int) -> str:
    hours = ", ".join(str(hour) for hour in sorted(parse_check_hours()))
    return "\n".join([
        f"<b>{escape(BOT_DISPLAY_NAME)}</b>",
        "",
        "Команди:",
        "/menu - відкрити приватне меню інструментів",
        "/postmenu - опублікувати меню в основний канал",
        "/status - показати стан бази",
        "/check - запустити бойову перевірку зараз",
        "/dryrun - перевірити без запису в Google Sheet і без повідомлень",
        "/cancel - скасувати поточний запит у меню",
        "/chatid - показати ID цього чату",
        "/help - допомога",
        "",
        f"Автоматичні перевірки: <b>{escape(hours)}</b> за timezone <b>{escape(BOT_TIMEZONE)}</b>.",
        f"Поточний chat id: <code>{escape(chat_id)}</code>",
    ])


def unauthorized_text(chat_id: str | int) -> str:
    return "\n".join([
        "Цей чат не доданий у TELEGRAM_CHAT_ID / TELEGRAM_ALLOWED_CHAT_IDS.",
        f"Chat ID: <code>{escape(chat_id)}</code>",
    ])


def is_authorized_chat(chat_id: str | int) -> bool:
    allowed = allowed_chat_ids()
    return not allowed or str(chat_id) in allowed


def can_use_private_menu(chat: dict) -> bool:
    return (
        str(chat.get("type") or "") == "private"
        and (TELEGRAM_PRIVATE_MENU_OPEN or is_authorized_chat(chat.get("id")))
    )


def bot_username() -> str:
    global bot_username_cache
    if bot_username_cache:
        return bot_username_cache
    try:
        data = bot_api("getMe", {}, timeout=10)
        username = ((data.get("result") or {}).get("username") or "").strip().lstrip("@")
        if username:
            bot_username_cache = username
    except Exception as e:
        print(json.dumps({
            "ok": False,
            "event": "bot_username_lookup_failed",
            "error": str(e),
        }, ensure_ascii=False))
    return bot_username_cache


def private_start_url(action: str) -> str:
    username = bot_username()
    if not username:
        return ""
    return f"https://t.me/{username}?start={action}"


def private_menu_markup() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "🌍 Availability", "callback_data": "menu:availability"},
                {"text": "📦 Overview", "callback_data": "menu:overview"},
            ],
            [
                {"text": "🔗 Geo Link", "callback_data": "menu:geolink"},
                {"text": "🔎 Indexing", "callback_data": "menu:indexing"},
            ],
            [
                {"text": "📚 Live DB", "callback_data": "menu:livedb"},
                {"text": "✅ Status", "callback_data": "menu:status"},
            ],
        ],
    }


def channel_menu_markup() -> dict | None:
    actions = [
        ("🌍 Availability", "availability"),
        ("📦 Overview", "overview"),
        ("🔗 Geo Link", "geolink"),
        ("🔎 Indexing", "indexing"),
        ("📚 Live DB", "livedb"),
    ]
    rows = []
    for idx in range(0, len(actions), 2):
        row = []
        for text, action in actions[idx:idx + 2]:
            url = private_start_url(action)
            if not url:
                return None
            row.append({"text": text, "url": url})
        rows.append(row)
    return {"inline_keyboard": rows}


def send_private_menu(chat_id: str | int):
    return send_message(
        chat_id,
        "\n".join([
            "<b>Меню WWA ASO Tools</b>",
            "",
            "Обери інструмент. Результат прийде сюди, у приватний чат, тому його бачитимеш тільки ти.",
        ]),
        reply_markup=private_menu_markup(),
    )


def send_channel_menu(chat_id: str | int):
    markup = channel_menu_markup()
    if not markup:
        send_message(
            chat_id,
            "Не можу створити меню: додай env <code>TELEGRAM_BOT_USERNAME</code> або дай боту доступ до getMe.",
        )
        return
    send_message(
        chat_id,
        "\n".join([
            "<b>WWA ASO Tools menu</b>",
            "",
            "Натисни потрібний інструмент. Бот відкриє приватний чат і покаже результат тільки тобі.",
        ]),
        reply_markup=markup,
    )


def set_user_session(chat_id: str | int, action: str):
    user_sessions[str(chat_id)] = {"action": action, "created_at": time.time()}


def clear_user_session(chat_id: str | int):
    user_sessions.pop(str(chat_id), None)


def parse_app_id_input(raw_value: str) -> str:
    return normalize_android_package_input(raw_value)


def truncate_text(value: str, max_len: int = 900) -> str:
    value = str(value or "").strip()
    if len(value) <= max_len:
        return value
    return value[:max_len].rstrip() + "…"


def prompt_for_action(chat_id: str | int, action: str):
    prompts = {
        "availability": "\n".join([
            "<b>Availability</b>",
            "Встав Google Play URL або package name.",
            "",
            "Приклад:",
            "<code>com.dragonplus.cookingfr</code>",
        ]),
        "overview": "\n".join([
            "<b>Overview</b>",
            "Введи package name або Google Play / Sensor Tower URL.",
            "Країну можна додати з нового рядка або після package. Якщо не вказати, буде AU.",
            "",
            "Приклад:",
            "<code>com.dragonplus.cookingfr AU</code>",
        ]),
        "geolink": "\n".join([
            "<b>Geo Link</b>",
            "Введи package/Google Play URL і країну.",
            "",
            "Приклад:",
            "<code>com.dragonplus.cookingfr Ukraine</code>",
        ]),
        "indexing": "\n".join([
            "<b>Keyword Indexing</b>",
            "Введи дані у такому форматі:",
            "",
            "<code>com.dragonplus.cookingfr",
            "US, AU, GB",
            "casino",
            "slot games",
            "fruit win</code>",
        ]),
        "livedb": "\n".join([
            "<b>Live DB</b>",
            "Введи package/name для пошуку в базі.",
            "Можна написати <code>all</code>, щоб показати перші додатки зі списку.",
        ]),
    }
    if action not in prompts:
        return
    set_user_session(chat_id, action)
    send_message(chat_id, prompts[action])


def remove_app_part(raw_text: str, app_id: str) -> str:
    text = re.sub(r"https?://\S+", " ", raw_text or "")
    if app_id:
        text = text.replace(app_id, " ")
    return re.sub(r"\s+", " ", text).strip(" ,;:-")


def country_from_google_play_url(raw_text: str) -> str:
    for match in re.finditer(r"https?://\S+", raw_text or ""):
        parsed = urlparse(match.group(0))
        if "play.google.com" not in parsed.netloc.lower():
            continue
        gl = (parse_qs(parsed.query).get("gl") or [""])[0]
        if re.fullmatch(r"[A-Za-z]{2}", gl or ""):
            return gl.upper()
    return ""


def parse_package_country_input(raw_text: str, default_country: str = "") -> tuple[str, str]:
    lines = [line.strip() for line in (raw_text or "").splitlines() if line.strip()]
    app_source = lines[0] if lines else raw_text
    app_id = parse_app_id_input(app_source or raw_text)
    country_raw = ""
    if len(lines) >= 2:
        country_raw = " ".join(lines[1:])
    else:
        country_raw = remove_app_part(raw_text, app_id)
        if not country_raw:
            country_raw = country_from_google_play_url(raw_text)
    return app_id, (country_raw or default_country or "").strip()


def format_availability_result(app_id: str, payload: dict) -> str:
    open_codes = payload.get("open_codes") or []
    closed_codes = payload.get("closed_codes") or []
    transient_codes = payload.get("transient_codes") or []
    total = int(payload.get("total") or len(open_codes) + len(closed_codes) + len(transient_codes))

    lines = [
        "<b>Availability result</b>",
        "",
        f"App ID: <code>{escape(app_id)}</code>",
        f"Google Play: {escape(build_google_play_url(app_id, 'US', 'en'))}",
        "",
        f"Open: <b>{len(open_codes)}</b> / {total}",
        f"Closed: <b>{len(closed_codes)}</b>",
    ]
    if transient_codes:
        lines.append(f"Unclear/retry later: <b>{len(transient_codes)}</b>")
    lines.extend([
        "",
        "<b>Closed countries:</b>",
        escape(format_country_lines(closed_codes, max_items=90)),
    ])
    return "\n".join(lines)


def format_overview_result(payload: dict) -> str:
    release = payload.get("release_details") or {}
    description = payload.get("description") or {}
    categories = ", ".join(payload.get("categories") or []) or "—"
    availability = payload.get("availability") or {}

    lines = [
        f"<b>{escape(payload.get('name') or payload.get('app_id'))}</b>",
        "",
        f"App ID: <code>{escape(payload.get('app_id'))}</code>",
        f"Publisher: <b>{escape(payload.get('publisher_name'))}</b>",
        f"Country/Region: <b>{escape(payload.get('country_name'))}</b>",
        f"Categories: <b>{escape(categories)}</b>",
        f"Content rating: <b>{escape(payload.get('content_rating'))}</b>",
        f"Price: <b>{escape(payload.get('price'))}</b>",
        f"Install range: <b>{escape(payload.get('install_range'))}</b>",
        f"Downloads last month: <b>{escape(payload.get('downloads_last_month'))}</b>",
        f"Rating: <b>{escape(payload.get('rating') or '—')}</b> ({escape(payload.get('rating_count') or 0)})",
        "",
        "<b>Release details</b>",
        f"Version: {escape(release.get('current_version'))}",
        f"Last updated: {escape(release.get('last_updated'))}",
        f"Country release: {escape(release.get('country_release_date'))}",
        f"Worldwide release: {escape(release.get('worldwide_release_date'))}",
        f"Minimum OS: {escape(release.get('minimum_os_version'))}",
        f"File size: {escape(release.get('file_size'))}",
    ]

    if availability:
        lines.extend([
            "",
            "<b>Availability</b>",
            f"Open: <b>{escape(availability.get('open_count') or 0)}</b>",
            f"Closed: <b>{escape(availability.get('closed_count') or 0)}</b>",
        ])

    short_description = truncate_text(description.get("short") or "", 500)
    full_description = truncate_text(description.get("full") or "", 1300)
    if short_description:
        lines.extend(["", "<b>Short description</b>", escape(short_description)])
    if full_description:
        lines.extend(["", "<b>Long description</b>", escape(full_description)])

    lines.extend([
        "",
        f"Google Play: {escape(payload.get('google_play_url'))}",
        f"Sensor Tower: {escape(payload.get('sensor_tower_url'))}",
    ])
    return "\n".join(lines)


def format_geo_link_result(app_id: str, country_raw: str) -> str:
    resolved = resolve_country_for_geo_link(country_raw)
    if not resolved:
        return f"Країну не знайдено: <code>{escape(country_raw)}</code>"
    gl, hl = resolved
    url = build_google_play_url(app_id, gl, hl)
    return "\n".join([
        "<b>Geo Link</b>",
        "",
        f"App ID: <code>{escape(app_id)}</code>",
        f"Country: <b>{escape(country_label(gl))}</b>",
        f"gl: <code>{escape(gl)}</code>",
        f"hl: <code>{escape(hl)}</code>",
        "",
        escape(url),
    ])


def parse_indexing_input(raw_text: str) -> tuple[str, list[dict], list[str], int, list[str]]:
    lines = [line.strip() for line in (raw_text or "").splitlines() if line.strip()]
    if len(lines) < 3:
        return "", [], [], 0, ["Потрібно мінімум 3 рядки: package, країни, keywords."]
    app_id = parse_app_id_input(lines[0])
    countries, country_errors = normalize_indexing_countries(lines[1])
    keywords, keyword_errors = normalize_indexing_keywords("\n".join(lines[2:]))
    errors = []
    if not app_id:
        errors.append("Не знайшов package name у першому рядку.")
    errors.extend(country_errors)
    errors.extend(keyword_errors)
    return app_id, countries, keywords, normalize_indexing_limit(100), errors


def format_indexing_result(payload: dict) -> str:
    stats = payload.get("stats") or {}
    rows = payload.get("rows") or []
    lines = [
        "<b>Keyword Indexing result</b>",
        "",
        f"App ID: <code>{escape(payload.get('app_id'))}</code>",
        f"Countries: <b>{len(payload.get('countries') or [])}</b>",
        f"Keywords: <b>{len(payload.get('keywords') or [])}</b>",
        f"Search top: <b>{escape(payload.get('limit'))}</b>",
        "",
        f"Indexed: <b>{escape(stats.get('indexed') or 0)}</b> / {escape(stats.get('total_checks') or len(rows))}",
        f"Not found: <b>{escape(stats.get('not_found') or 0)}</b>",
        f"Errors: <b>{escape(stats.get('errors') or 0)}</b>",
    ]
    if stats.get("best_rank"):
        lines.append(f"Best rank: <b>#{escape(stats.get('best_rank'))}</b>")

    lines.extend(["", "<b>Rows</b>"])
    for row in rows[:35]:
        status = "✅" if row.get("indexed") else ("⚠️" if row.get("status") == "error" else "—")
        rank = f"#{row.get('rank')}" if row.get("rank") else "not in top"
        if row.get("status") == "error":
            rank = row.get("error") or "error"
        lines.append(
            f"{status} <b>{escape(row.get('gl'))}</b> · {escape(row.get('keyword'))}: {escape(rank)}"
        )
    if len(rows) > 35:
        lines.append(f"…and {len(rows) - 35} more")
    return "\n".join(lines)


def format_live_db_search_result(query: str, payload: dict) -> str:
    apps = payload.get("apps") or []
    stats = payload.get("stats") or {}
    raw_query = (query or "").strip()
    query_key = raw_query.casefold()
    if query_key and query_key != "all":
        apps = [
            app for app in apps
            if query_key in str(app.get("app_id") or "").casefold()
            or query_key in str(app.get("app_name") or "").casefold()
            or query_key in str(app.get("app_url") or "").casefold()
        ]
    limit = TELEGRAM_LIVE_DB_MENU_LIMIT
    lines = [
        "<b>Live DB</b>",
        "",
        f"Apps: <b>{escape(stats.get('total') or 0)}</b>",
        f"Live: <b>{escape(stats.get('live') or 0)}</b>",
        f"With closed GEO: <b>{escape(stats.get('with_closed') or 0)}</b>",
    ]
    if raw_query and query_key != "all":
        lines.append(f"Search: <code>{escape(raw_query)}</code>")
    lines.append("")
    if not apps:
        lines.append("Нічого не знайдено.")
        return "\n".join(lines)

    for app in apps[:limit]:
        closed_codes = app.get("closed_codes") or []
        closed_line = format_country_lines(closed_codes, max_items=18)
        lines.extend([
            f"<b>{escape(app.get('app_name') or app.get('app_id'))}</b>",
            f"<code>{escape(app.get('app_id'))}</code>",
            f"Status: <b>{'Live' if app.get('is_live') else 'Watch'}</b>",
            f"Open: <b>{escape(app.get('open_count') or 0)}</b> · Closed: <b>{escape(app.get('closed_count') or 0)}</b>",
            f"Closed GEO: {escape(closed_line)}",
            "",
        ])
    if len(apps) > limit:
        lines.append(f"…and {len(apps) - limit} more. Уточни пошук по bundle/name.")
    return "\n".join(lines).strip()


def process_menu_request(chat_id: str | int, action: str, text: str):
    try:
        safe_send_message(chat_id, "Обробляю запит. Зазвичай це займає від кількох секунд до кількох хвилин.")

        if action == "availability":
            app_id = parse_app_id_input(text)
            if not app_id:
                send_message(chat_id, "Не знайшов package name. Встав Google Play URL або bundle.")
                return
            payload = summarize_google_availability(app_id)
            send_long_message(chat_id, format_availability_result(app_id, payload))
            return

        if action == "overview":
            app_id, country_raw = parse_package_country_input(text, default_country="AU")
            if not app_id:
                send_message(chat_id, "Не знайшов package name. Встав Google Play / Sensor Tower URL або bundle.")
                return
            resolved = resolve_country_for_geo_link(country_raw or "AU")
            country = resolved[0] if resolved else "AU"
            payload, error = build_app_overview_payload(app_id, country)
            if error or not payload:
                send_message(chat_id, f"Overview error: <code>{escape(error or 'UNKNOWN')}</code>")
                return
            send_long_message(chat_id, format_overview_result(payload))
            return

        if action == "geolink":
            app_id, country_raw = parse_package_country_input(text)
            if not app_id:
                send_message(chat_id, "Не знайшов package name. Встав Google Play URL або bundle.")
                return
            if not country_raw:
                send_message(chat_id, "Не знайшов країну. Приклад: <code>com.app Ukraine</code>")
                return
            send_message(chat_id, format_geo_link_result(app_id, country_raw))
            return

        if action == "indexing":
            app_id, countries, keywords, limit, errors = parse_indexing_input(text)
            if errors:
                send_message(chat_id, "\n".join(["<b>Indexing input error</b>", *[f"• {escape(e)}" for e in errors]]))
                return
            payload = build_google_play_indexing_payload(app_id, keywords, countries, limit)
            send_long_message(chat_id, format_indexing_result(payload))
            return

        if action == "livedb":
            payload = build_live_apps_database_payload()
            send_long_message(chat_id, format_live_db_search_result(text, payload))
            return

        send_message(chat_id, "Невідомий пункт меню. Натисни /menu.")
    except Exception as e:
        safe_send_message(chat_id, f"<b>Помилка запиту:</b>\n<code>{escape(e)}</code>")
    finally:
        clear_user_session(chat_id)


def handle_session_message(message: dict) -> bool:
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text or str(chat.get("type") or "") != "private":
        return False
    session_state = user_sessions.get(str(chat_id))
    if not session_state:
        return False
    action = session_state.get("action") or ""
    threading.Thread(target=process_menu_request, args=(chat_id, action, text), daemon=True).start()
    return True


def run_check_async(chat_id: str | int, dry_run: bool = False):
    def worker():
        if not check_lock.acquire(blocking=False):
            safe_send_message(chat_id, "Перевірка вже йде. Дочекайся завершення.")
            return

        global active_check_state
        try:
            mode = "dry run" if dry_run else "live run"
            active_check_state = {
                "mode": mode,
                "started_at": datetime.now(ZoneInfo(BOT_TIMEZONE)).strftime("%H:%M:%S"),
                "app_index": 0,
                "apps_to_check": 0,
                "errors": 0,
                "notifications": 0,
                "skipped": 0,
            }
            last_progress = {"app_index": 0, "sent_at": 0.0}

            def report_progress(progress: dict):
                active_check_state.update(progress)
                app_index = int(progress.get("app_index") or 0)
                apps_to_check = int(progress.get("apps_to_check") or 0)
                now_ts = time.time()
                should_send = (
                    app_index >= apps_to_check
                    or app_index - int(last_progress["app_index"]) >= BOT_PROGRESS_EVERY_APPS
                    or now_ts - float(last_progress["sent_at"]) >= 180
                )
                if not should_send:
                    return
                last_progress["app_index"] = app_index
                last_progress["sent_at"] = now_ts
                safe_send_message(
                    chat_id,
                    "\n".join([
                        f"<b>Прогрес {escape(mode)}</b>",
                        f"Apps: <b>{app_index}</b> / {apps_to_check}",
                        f"Errors: <b>{progress.get('errors', 0)}</b>",
                        f"Notifications: <b>{progress.get('notifications', 0)}</b>",
                    ]),
                )

            safe_send_message(chat_id, f"Запускаю <b>{escape(mode)}</b>. Це може зайняти кілька хвилин.")
            result = run_availability_bot_check(
                send_messages=not dry_run,
                write_changes=not dry_run,
                limit=BOT_MAX_COMMAND_CHECKS,
                progress_callback=report_progress,
            )
            result["dry_run"] = dry_run
            safe_send_message(chat_id, summary_text(result, "Manual availability check finished"))
        except Exception as e:
            safe_send_message(chat_id, f"<b>Помилка перевірки:</b>\n<code>{escape(e)}</code>")
        finally:
            active_check_state = {}
            check_lock.release()

    threading.Thread(target=worker, daemon=True).start()


def run_scheduled_check():
    if not check_lock.acquire(blocking=False):
        return

    try:
        result = run_availability_bot_check(send_messages=True, write_changes=True, limit=AVAILABILITY_CHECK_LIMIT)
        if TELEGRAM_CHAT_ID and (
            TELEGRAM_SEND_EMPTY_SUMMARY
            or result.get("notifications")
            or result.get("errors")
        ):
            safe_send_message(TELEGRAM_CHAT_ID, summary_text(result, scheduled_summary_title(result)))
    except Exception:
        if TELEGRAM_CHAT_ID:
            safe_send_message(
                TELEGRAM_CHAT_ID,
                "<b>Scheduled availability check failed</b>\n"
                f"<code>{escape(traceback.format_exc()[-2500:])}</code>",
            )
    finally:
        check_lock.release()


def send_status(chat_id: str | int):
    try:
        store = GoogleSheetsAvailabilityStore()
        apps = store.load_apps()
        live = sum(1 for app in apps if str(app.get("status") or "").lower() == "live")
        watch = len(apps) - live
        send_message(
            chat_id,
            "\n".join([
                f"<b>{escape(BOT_DISPLAY_NAME)} status</b>",
                "",
                f"Apps in database: <b>{len(apps)}</b>",
                f"Live: <b>{live}</b>",
                f"Watch: <b>{watch}</b>",
                f"Check hours: <b>{escape(BOT_CHECK_HOURS)}</b>",
                f"Timezone: <b>{escape(BOT_TIMEZONE)}</b>",
                "",
                (
                    f"Current check: <b>{escape(active_check_state.get('mode'))}</b> "
                    f"{active_check_state.get('app_index', 0)} / {active_check_state.get('apps_to_check', 0)}"
                    if active_check_state else "Current check: <b>none</b>"
                ),
            ]),
        )
    except Exception as e:
        send_message(chat_id, f"<b>Status error:</b>\n<code>{escape(e)}</code>")


def maybe_run_schedule():
    global last_scheduled_key

    try:
        now = datetime.now(ZoneInfo(BOT_TIMEZONE))
    except Exception:
        print(json.dumps({
            "ok": False,
            "event": "invalid_timezone",
            "timezone": BOT_TIMEZONE,
            "fallback": "UTC",
        }, ensure_ascii=False))
        now = datetime.now(ZoneInfo("UTC"))
    if now.hour not in parse_check_hours():
        return
    if now.minute >= BOT_SCHEDULE_GRACE_MINUTES:
        return

    key = f"{now.date().isoformat()}-{now.hour}"
    if key == last_scheduled_key:
        return

    last_scheduled_key = key
    threading.Thread(target=run_scheduled_check, daemon=True).start()


def handle_message(message: dict):
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    chat_type = str(chat.get("type") or "")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return

    if not text.startswith("/"):
        handle_session_message(message)
        return

    parts = text.split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lower()
    command_arg = parts[1].strip() if len(parts) > 1 else ""

    if command == "/chatid":
        send_message(chat_id, f"Chat ID: <code>{escape(chat_id)}</code>")
        return

    if command == "/cancel":
        clear_user_session(chat_id)
        send_message(chat_id, "Поточний запит скасовано.")
        return

    if command == "/start" and chat_type == "private":
        action = command_arg.strip().lower()
        if can_use_private_menu(chat) and action in {"availability", "overview", "geolink", "indexing", "livedb"}:
            prompt_for_action(chat_id, action)
            return
        if can_use_private_menu(chat):
            send_private_menu(chat_id)
            return

    if command == "/menu":
        if chat_type == "private":
            if not can_use_private_menu(chat):
                send_message(chat_id, unauthorized_text(chat_id))
                return
            send_private_menu(chat_id)
            return
        if is_authorized_chat(chat_id):
            send_channel_menu(chat_id)
            return

    if not is_authorized_chat(chat_id):
        send_message(chat_id, unauthorized_text(chat_id))
        return

    if command == "/postmenu":
        target_chat_id = TELEGRAM_CHAT_ID or chat_id
        send_channel_menu(target_chat_id)
        if str(target_chat_id) != str(chat_id):
            send_message(chat_id, f"Меню відправлено в канал <code>{escape(target_chat_id)}</code>.")
        return

    if command in {"/start", "/help"}:
        send_message(chat_id, help_text(chat_id))
        return

    if command == "/status":
        send_status(chat_id)
        return

    if command == "/check":
        run_check_async(chat_id, dry_run=False)
        return

    if command in {"/dryrun", "/dry_run", "/dry"}:
        run_check_async(chat_id, dry_run=True)
        return

    send_message(chat_id, "Невідома команда. Напиши /help")


def handle_callback_query(query: dict):
    query_id = query.get("id")
    message = query.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    data = (query.get("data") or "").strip()
    if not query_id or not chat_id:
        return

    try:
        answer_callback_query(query_id)
    except Exception:
        pass

    if not can_use_private_menu(chat):
        safe_send_message(chat_id, unauthorized_text(chat_id))
        return

    if not data.startswith("menu:"):
        safe_send_message(chat_id, "Невідома кнопка. Натисни /menu.")
        return

    action = data.split(":", 1)[1]
    if action == "status":
        send_status(chat_id)
        return
    if action in {"availability", "overview", "geolink", "indexing", "livedb"}:
        prompt_for_action(chat_id, action)
        return
    if action == "home":
        send_private_menu(chat_id)
        return

    safe_send_message(chat_id, "Невідомий пункт меню. Натисни /menu.")


def drop_pending_updates() -> int:
    if not BOT_DROP_PENDING_UPDATES:
        return 0
    data = bot_api("getUpdates", {"timeout": 0}, timeout=10)
    updates = data.get("result") or []
    if not updates:
        return 0
    return max(int(item.get("update_id", 0)) for item in updates) + 1


def set_bot_commands():
    commands = [
        {"command": "menu", "description": "Відкрити меню інструментів"},
        {"command": "postmenu", "description": "Опублікувати меню в канал"},
        {"command": "status", "description": "Показати стан бази"},
        {"command": "check", "description": "Запустити бойову перевірку"},
        {"command": "dryrun", "description": "Перевірити без запису і повідомлень"},
        {"command": "cancel", "description": "Скасувати поточний запит"},
        {"command": "chatid", "description": "Показати ID чату"},
        {"command": "help", "description": "Допомога"},
    ]
    try:
        bot_api("setMyCommands", {"commands": commands}, timeout=10)
    except Exception as e:
        print(json.dumps({
            "ok": False,
            "event": "set_commands_failed",
            "error": str(e),
        }, ensure_ascii=False))


def polling_loop():
    set_bot_commands()
    offset = drop_pending_updates()
    print(json.dumps({
        "ok": True,
        "event": "bot_started",
        "bot": BOT_DISPLAY_NAME,
        "timezone": BOT_TIMEZONE,
        "check_hours": sorted(parse_check_hours()),
        "allowed_chats": sorted(allowed_chat_ids()),
    }, ensure_ascii=False))

    while True:
        maybe_run_schedule()
        try:
            data = bot_api("getUpdates", {
                "offset": offset,
                "timeout": BOT_POLL_TIMEOUT,
                "allowed_updates": ["message", "channel_post", "callback_query"],
            }, timeout=BOT_POLL_TIMEOUT + 10)
            for update in data.get("result") or []:
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                if update.get("message"):
                    handle_message(update["message"])
                if update.get("channel_post"):
                    handle_message(update["channel_post"])
                if update.get("callback_query"):
                    handle_callback_query(update["callback_query"])
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(json.dumps({
                "ok": False,
                "event": "polling_error",
                "error": str(e),
            }, ensure_ascii=False))
            time.sleep(5)


if __name__ == "__main__":
    polling_loop()
