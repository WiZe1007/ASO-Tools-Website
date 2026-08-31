"""Shared, administrator-only account management for the two WWA sites."""

import hashlib
import hmac
import re
import secrets
from pathlib import Path

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


def account_session_token(user):
    # Bind sessions to the password without putting a password hash in cookies.
    payload = f"{user['email']}:{user['password_hash']}".encode()
    return hmac.new(str(current_app.secret_key).encode(), payload, hashlib.sha256).hexdigest()


def account_session_valid(user):
    return bool(
        user
        and int(user.get("active") or 0) == 1
        and secrets.compare_digest(str(session.get("account_token") or ""), account_session_token(user))
    )


def verify_account_password(user, password):
    if not user or int(user.get("active") or 0) != 1:
        return False
    try:
        return check_password_hash(user.get("password_hash") or "", password)
    except (ValueError, TypeError):
        return False


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


def create_accounts_blueprint(*, list_users, get_user, create_user, update_user, domain, site_name, home_endpoint, storage_errors):
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
                {key: user.get(key) for key in ("email", "active", "created_at", "last_login_at")}
                for user in list_users()
            ]
        except storage_errors:
            users = []
            error, status = "Сховище користувачів тимчасово недоступне. Спробуй пізніше.", 503
        return render_template(
            "accounts/users.html", users=sorted(users, key=lambda user: user["email"]),
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
        try:
            create_user(email, password)
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
        flash("Пароль змінено." if action == "password" else "Статус користувача змінено.")
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
