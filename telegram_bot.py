import html
import json
import os
import threading
import time
import traceback
from datetime import datetime
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
    run_availability_bot_check,
    session,
)


def env_int(name: str, default: int, min_value: int = 1, max_value: int = 3600) -> int:
    try:
        value = int(os.environ.get(name, default))
    except Exception:
        value = default
    return max(min_value, min(max_value, value))


BOT_TIMEZONE = os.environ.get("BOT_TIMEZONE", "Europe/Kiev").strip() or "Europe/Kiev"
BOT_CHECK_HOURS = os.environ.get("BOT_CHECK_HOURS", "9,15,21").strip() or "9,15,21"
BOT_POLL_TIMEOUT = env_int("BOT_POLL_TIMEOUT", 25, 5, 50)
BOT_DROP_PENDING_UPDATES = os.environ.get("BOT_DROP_PENDING_UPDATES", "1").strip() != "0"
BOT_MAX_COMMAND_CHECKS = env_int("BOT_MAX_COMMAND_CHECKS", AVAILABILITY_CHECK_LIMIT, 1, 1000)
BOT_SCHEDULE_GRACE_MINUTES = env_int("BOT_SCHEDULE_GRACE_MINUTES", 10, 1, 59)
BOT_API_RETRIES = env_int("BOT_API_RETRIES", 4, 1, 8)
BOT_PROGRESS_EVERY_APPS = env_int("BOT_PROGRESS_EVERY_APPS", 10, 1, 100)

check_lock = threading.Lock()
last_scheduled_key = ""
active_check_state: dict = {}


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


def send_message(chat_id: str | int, text: str):
    return bot_api("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


def safe_send_message(chat_id: str | int, text: str):
    try:
        return send_message(chat_id, text)
    except Exception as e:
        print(json.dumps({
            "ok": False,
            "event": "telegram_send_failed",
            "chat_id": str(chat_id),
            "error": str(e),
        }, ensure_ascii=False))
        return None


def escape(value) -> str:
    return html.escape(str(value or ""))


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
                f"• {escape(item.get('event'))}: <code>{escape(item.get('app_id'))}</code> "
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


def help_text(chat_id: str | int) -> str:
    hours = ", ".join(str(hour) for hour in sorted(parse_check_hours()))
    return "\n".join([
        "<b>WWA ASO Availability Bot</b>",
        "",
        "Команди:",
        "/status - показати стан бази",
        "/check - запустити бойову перевірку зараз",
        "/dryrun - перевірити без запису в Google Sheet і без повідомлень",
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
        if os.environ.get("TELEGRAM_SEND_EMPTY_SUMMARY", "0").strip() == "1" and TELEGRAM_CHAT_ID:
            safe_send_message(TELEGRAM_CHAT_ID, summary_text(result, "Scheduled availability check finished"))
    except Exception:
        if TELEGRAM_CHAT_ID:
            safe_send_message(
                TELEGRAM_CHAT_ID,
                "<b>Scheduled availability check failed</b>\n"
                f"<code>{escape(traceback.format_exc()[-2500:])}</code>",
            )
    finally:
        check_lock.release()


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
    text = (message.get("text") or "").strip()
    if not chat_id or not text.startswith("/"):
        return

    command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()

    if command == "/chatid":
        send_message(chat_id, f"Chat ID: <code>{escape(chat_id)}</code>")
        return

    if not is_authorized_chat(chat_id):
        send_message(chat_id, unauthorized_text(chat_id))
        return

    if command in {"/start", "/help"}:
        send_message(chat_id, help_text(chat_id))
        return

    if command == "/status":
        try:
            store = GoogleSheetsAvailabilityStore()
            apps = store.load_apps()
            live = sum(1 for app in apps if str(app.get("status") or "").lower() == "live")
            watch = len(apps) - live
            send_message(
                chat_id,
                "\n".join([
                    "<b>Bot status</b>",
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
        return

    if command == "/check":
        run_check_async(chat_id, dry_run=False)
        return

    if command in {"/dryrun", "/dry_run", "/dry"}:
        run_check_async(chat_id, dry_run=True)
        return

    send_message(chat_id, "Невідома команда. Напиши /help")


def drop_pending_updates() -> int:
    if not BOT_DROP_PENDING_UPDATES:
        return 0
    data = bot_api("getUpdates", {"timeout": 0}, timeout=10)
    updates = data.get("result") or []
    if not updates:
        return 0
    return max(int(item.get("update_id", 0)) for item in updates) + 1


def polling_loop():
    offset = drop_pending_updates()
    print(json.dumps({
        "ok": True,
        "event": "bot_started",
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
                "allowed_updates": ["message"],
            }, timeout=BOT_POLL_TIMEOUT + 10)
            for update in data.get("result") or []:
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                if update.get("message"):
                    handle_message(update["message"])
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
