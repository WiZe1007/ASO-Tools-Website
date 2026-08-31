"""Shared, administrator-only account management for the two WWA sites."""

import hashlib
import hmac
import math
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlsplit

import click
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash


TEAM_EMAILS = (
    "bohdan.m.publish@wildwildgroup.com",
    "artem.k.publish@wildwildgroup.com",
    "vladyslav.s.publish@wildwildgroup.com",
    "mykhailo.h.android.dev@wildwildgroup.com",
    "cto@wildwildgroup.com",
)

DATABASE_ACCESS_KEYS = {"wwa", "s"}
_DUMMY_PASSWORD_HASH = (
    "scrypt:32768:8:1$aj5JqKI6XYLGmdzA$"
    "8f9279a3488a50a82c4ae41d9cd8047d8f2f6459bd8dac5a9d93e6de83594889"
    "e55e36aea0c1d4b78d08cda5eb18d0d9aa272490a909d55121d28f7aefd93cbf"
)


def hosted_runtime_detected():
    return any(
        str(os.environ.get(name) or "").strip()
        for name in ("RENDER", "RENDER_SERVICE_ID", "RENDER_EXTERNAL_HOSTNAME")
    )


class LoginAttemptLimiter:
    """Small per-process throttle for repeated login failures."""

    def __init__(self, max_failures=6, window_seconds=15 * 60, max_keys=5000):
        self.max_failures = max(1, int(max_failures))
        self.window_seconds = max(1, int(window_seconds))
        self.max_keys = max(100, int(max_keys))
        self._attempts = defaultdict(deque)
        self._lock = threading.Lock()

    def _key(self, identity):
        return hashlib.sha256(str(identity or "").encode()).hexdigest()

    def _trim(self, attempts, now):
        cutoff = now - self.window_seconds
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()

    def retry_after(self, identity):
        key = self._key(identity)
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts.get(key)
            if not attempts:
                return 0
            self._trim(attempts, now)
            if not attempts:
                self._attempts.pop(key, None)
                return 0
            if len(attempts) < self.max_failures:
                return 0
            return max(1, math.ceil(self.window_seconds - (now - attempts[0])))

    def record_failure(self, identity):
        key = self._key(identity)
        now = time.monotonic()
        with self._lock:
            if len(self._attempts) >= self.max_keys and key not in self._attempts:
                oldest = min(self._attempts, key=lambda item: self._attempts[item][0] if self._attempts[item] else now)
                self._attempts.pop(oldest, None)
            attempts = self._attempts[key]
            self._trim(attempts, now)
            attempts.append(now)
        return self.retry_after(identity)

    def clear(self, identity=None):
        with self._lock:
            if identity is None:
                self._attempts.clear()
            else:
                self._attempts.pop(self._key(identity), None)


def login_limit_identity(email, remote_addr):
    normalized_email = str(email or "").strip().lower()[:254]
    normalized_remote = str(remote_addr or "unknown").strip().lower()[:128]
    return f"{normalized_email}|{normalized_remote}"


def request_origin_allowed():
    """Reject browser writes coming from another origin without trusting the proxy scheme."""
    fetch_site = (request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if fetch_site not in {"", "none", "same-origin"}:
        return False
    origin = (request.headers.get("Origin") or "").strip()
    source_url = origin or (request.headers.get("Referer") or "").strip()
    if not source_url:
        return True
    if source_url.casefold() == "null":
        return fetch_site == "same-origin"
    try:
        parsed = urlsplit(source_url)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.netloc.casefold() == request.host.casefold()


def apply_security_headers(response):
    response.headers.setdefault("Content-Security-Policy", (
        "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
        "form-action 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; font-src 'self' data: https:; connect-src 'self'"
    ))
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
    if current_app.config.get("SESSION_COOKIE_SECURE"):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.endpoint not in {"static", "health"}:
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def serialize_database_access(values):
    selected = set(values)
    if not selected <= DATABASE_ACCESS_KEYS:
        raise ValueError("INVALID_DATABASE_ACCESS")
    return ",".join(sorted(selected)) or "none"


def account_database_access(user, legacy_s_emails=""):
    if not user:
        return set()
    stored = str(user.get("database_access") or "").strip().lower()
    if stored:
        return set(stored.split(",")) & DATABASE_ACCESS_KEYS
    # Old accounts keep their previous rights until an admin saves explicit access.
    legacy_allowed = {item.strip().lower() for item in re.split(r"[\s,;]+", legacy_s_emails) if item.strip()}
    return {"wwa", "s"} if user["email"] in legacy_allowed else {"wwa"}


def account_session_token(user):
    # Bind sessions to the password without putting a password hash in cookies.
    payload = f"{current_app.config.get('AUTH_ACCOUNT_REALM', '')}:{user['email']}:{user['password_hash']}".encode()
    return hmac.new(str(current_app.secret_key).encode(), payload, hashlib.sha256).hexdigest()


def account_session_valid(user):
    return bool(
        user
        and int(user.get("active") or 0) == 1
        and secrets.compare_digest(str(session.get("account_token") or ""), account_session_token(user))
    )


def verify_account_password(user, password):
    try:
        eligible = bool(user and int(user.get("active") or 0) == 1)
    except (TypeError, ValueError):
        eligible = False
    password_hash = str(user.get("password_hash") or "") if eligible else _DUMMY_PASSWORD_HASH
    try:
        verified = check_password_hash(password_hash, password)
    except (ValueError, TypeError):
        # Keep malformed account rows from becoming a timing oracle.
        check_password_hash(_DUMMY_PASSWORD_HASH, password)
        return False
    return eligible and verified


def account_validation_error(email, password, domain):
    if (
        len(email) > 254
        or not re.fullmatch(r"[a-z0-9][a-z0-9._%+\-]*@[a-z0-9][a-z0-9.\-]*", email)
        or email.rsplit("@", 1)[-1] != str(domain).strip().lower().lstrip("@")
    ):
        return "Вкажи коректну корпоративну пошту."
    if not 8 <= len(password) <= 256 or not password.strip():
        return "Пароль має містити від 8 до 256 символів."
    return ""


def is_account_admin(user=None):
    user = user or getattr(g, "current_user", None)
    allowed = {
        item.strip().lower()
        for item in re.split(r"[\s,;]+", current_app.config.get("AUTH_ADMIN_EMAILS") or "")
        if item.strip()
    }
    return bool(user and int(user.get("active") or 0) == 1 and user["email"] in allowed)


def login_csrf_token():
    if not session.get("csrf_token"):
        session["csrf_token"] = secrets.token_urlsafe(24)
    return session["csrf_token"]


def valid_form_csrf():
    expected = str(session.get("csrf_token") or "")
    supplied = str(request.form.get("csrf_token") or "")
    return bool(expected and supplied and secrets.compare_digest(expected, supplied))


def create_accounts_blueprint(*, list_users, get_user, create_user, update_user, delete_user, domain, site_name, home_endpoint, storage_errors, database_access=None):
    blueprint = Blueprint(
        "accounts", __name__, url_prefix="/admin", cli_group="users",
        template_folder=str(Path(__file__).resolve().parent / "templates"),
    )

    @blueprint.after_request
    def prevent_account_page_caching(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @blueprint.before_request
    def require_admin():
        user = getattr(g, "current_user", None)
        if not user:
            abort(403)
        # Privileged requests always bypass the short session lookup cache.
        try:
            fresh_user = get_user(user["email"], fresh=True)
        except storage_errors:
            abort(503)
        if not account_session_valid(fresh_user) or not is_account_admin(fresh_user):
            abort(403)
        if request.method == "POST" and not valid_form_csrf():
            abort(403)

    def render_users(error="", status=200):
        try:
            users = [
                {**{key: user.get(key) for key in ("email", "active", "created_at", "last_login_at")},
                 "database_access": database_access(user) if database_access else set()}
                for user in list_users()
            ]
        except storage_errors:
            users = []
            error, status = "Сховище користувачів тимчасово недоступне. Спробуй пізніше.", 503
        group = request.values.get("group", "all")
        if not database_access or group not in DATABASE_ACCESS_KEYS:
            group = "all"
        counts = {"all": len(users), **{key: sum(key in user["database_access"] for user in users) for key in DATABASE_ACCESS_KEYS}}
        visible_users = users if group == "all" else [user for user in users if group in user["database_access"]]
        return render_template(
            "accounts/users.html", users=sorted(visible_users, key=lambda user: user["email"]),
            group=group, account_counts=counts,
            manage_database_access=database_access is not None,
            error=error, csrf_token=login_csrf_token(), domain=domain(),
            site_name=site_name, home_endpoint=home_endpoint,
            current_email=g.current_user["email"],
        ), status

    @blueprint.route("/users", methods=["GET", "POST"])
    def users():
        if request.method == "GET":
            return render_users()
        email = str(request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        error = account_validation_error(email, password, domain())
        if error:
            return render_users(error, 400)
        options = {}
        if database_access:
            try:
                options["database_access"] = serialize_database_access(request.form.getlist("database_access"))
            except ValueError:
                return render_users("Некоректний список доступів до баз даних.", 400)
        elif "database_access" in request.form:
            return render_users("Доступи до баз даних налаштовуються на сайті Бази Даних.", 400)
        try:
            create_user(email, password, **options)
        except ValueError:
            return render_users("Користувач із такою поштою вже існує.", 409)
        except storage_errors:
            return render_users("Не вдалося додати користувача. Перевір список перед повторною спробою.", 503)
        flash("Користувача додано.")
        return redirect(url_for("accounts.users"), code=303)

    @blueprint.post("/users/<email>")
    def edit_user(email):
        email = email.strip().lower()
        action = request.form.get("action")
        if action == "delete":
            if email == g.current_user["email"]:
                return render_users("Не можна видалити власний акаунт.", 400)
            if str(request.form.get("confirm_email") or "").strip().lower() != email:
                return render_users("Підтвердь видалення вибраного користувача.", 400)
            try:
                delete_user(email)
            except ValueError:
                abort(404)
            except storage_errors:
                return render_users("Не вдалося підтвердити видалення. Перевір список перед повторною спробою.", 503)
            flash("Користувача повністю видалено.")
            return redirect(url_for("accounts.users"), code=303)
        updates = {}
        if action == "password":
            password = request.form.get("password") or ""
            error = account_validation_error(email, password, domain())
            if error:
                return render_users(error, 400)
            updates["password"] = password
        elif action == "status" and request.form.get("active") in {"0", "1"}:
            updates["active"] = int(request.form["active"])
            if not updates["active"] and email == g.current_user["email"]:
                return render_users("Не можна вимкнути власний акаунт.", 400)
        elif action == "access" and database_access:
            try:
                updates["database_access"] = serialize_database_access(request.form.getlist("database_access"))
            except ValueError:
                return render_users("Некоректний список доступів до баз даних.", 400)
        else:
            abort(400)
        try:
            changed = update_user(email, **updates)
        except ValueError:
            abort(404)
        except storage_errors:
            return render_users("Не вдалося зберегти зміни. Спробуй пізніше.", 503)
        if email == g.current_user["email"]:
            session["account_token"] = account_session_token(changed)
        flash({"password": "Пароль змінено.", "status": "Статус користувача змінено.", "access": "Доступи до баз даних змінено."}[action])
        return redirect(url_for("accounts.users"), code=303)

    @blueprint.cli.command("add")
    @click.argument("email")
    @click.password_option(confirmation_prompt=True)
    def add_account(email, password):
        """Manually provision an account from a trusted server terminal."""
        email = email.strip().lower()
        error = account_validation_error(email, password, domain())
        if error:
            raise click.ClickException(error)
        try:
            create_user(email, password)
        except ValueError:
            raise click.ClickException("User already exists; the password was not changed.")
        except storage_errors:
            raise click.ClickException("Account storage unavailable; check the account list before retrying.")
        click.echo(f"Created {email}")

    @blueprint.cli.command("check-team")
    def check_team():
        """Verify existing team accounts without resetting passwords or creating users."""
        missing = False
        try:
            for email in TEAM_EMAILS:
                user = get_user(email, fresh=True)
                status = "active" if user and int(user.get("active") or 0) == 1 else "missing or inactive"
                click.echo(f"{email}: {status}")
                missing |= status != "active"
        except storage_errors:
            raise click.ClickException("Account storage unavailable.")
        if missing:
            raise click.ClickException("Some team accounts need manual provisioning or activation.")

    return blueprint
