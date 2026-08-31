# WWA and S Apps Database

This is a standalone Flask application for managing the Google Sheets database used by the availability bots.

## Local start

```bash
cd ~/Documents/WWA_ASO_Checker
DATABASE_SITE_AUTH_REQUIRED=0 \
DATABASE_SITE_SPREADSHEET_ID="your_sheet_id" \
DATABASE_SITE_SERVICE_ACCOUNT_FILE="/absolute/path/key.json" \
.venv/bin/python -m database_site.app
```

Open `http://127.0.0.1:8090`.

## Separate Render Web Service

Create a new Web Service from the same repository. This service is separate from `wwa-aso-checker`.

- Build command: `pip install -r requirements.txt`
- Start command for Render Free: `gunicorn database_site.app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`
- Health check path: `/health`

Required environment variables for the WWA database:

- `DATABASE_SITE_SPREADSHEET_ID` - Google Sheet ID.
- `DATABASE_SITE_SERVICE_ACCOUNT_JSON` - the complete service account JSON.
- `DATABASE_SITE_SECRET_KEY` - a stable random string, at least 32 characters.
- `DATABASE_SITE_ALLOWED_EMAIL_DOMAIN` - `@wildwildgroup.com`.
- `DATABASE_SITE_AUTH_STORAGE` - `google_sheets` for the Render Free plan.
- `DATABASE_SITE_USERS_SHEET` - optional users sheet name, defaults to `Users`.
- `DATABASE_SITE_SECURE_COOKIES` - `1` on Render.

With `DATABASE_SITE_AUTH_STORAGE=google_sheets`, the site automatically creates
the `Users` sheet in the WWA spreadsheet. Email addresses, password hashes,
account status, and login timestamps are stored there. Plain-text passwords are
never written to Google Sheets. This mode does not require a Render Disk and is
recommended for the Free plan.

For local-only development, `DATABASE_SITE_AUTH_STORAGE=sqlite` remains
available. `DATABASE_SITE_AUTH_DB` then controls the SQLite file path, but that
file is not persistent on Render Free.

Self-registration is disabled. `/register` returns 404 for both GET and POST.
The WWA database is available to active accounts provisioned manually by an
administrator. A corporate email address alone does not create an account.

## Manual Accounts Rollout

1. Keep the existing WWA Google Sheet and its `Users` tab. Do not delete or
   replace password hashes. The five requested accounts are reused in place:
   `bohdan.m.publish@wildwildgroup.com`, `artem.k.publish@wildwildgroup.com`,
   `vladyslav.s.publish@wildwildgroup.com`,
   `mykhailo.h.android.dev@wildwildgroup.com`, `cto@wildwildgroup.com`.
2. On both Web Services set `AUTH_SPREADSHEET_ID` to this same WWA spreadsheet,
   `AUTH_USERS_SHEET=Users`, and `AUTH_SERVICE_ACCOUNT_JSON` to credentials with
   Editor access to it. Existing `DATABASE_SITE_*`/`GOOGLE_SERVICE_ACCOUNT_*`
   credentials remain supported as fallbacks. Do not use the S spreadsheet for
   account storage.
3. Set `DATABASE_SITE_AUTH_STORAGE=google_sheets` on Apps Database and
   `AUTH_STORAGE=google_sheets` on WWA Tools. No Render Disk is needed.
4. Set `AUTH_ADMIN_EMAILS` on each service to the owner-approved existing
   administrator email(s). There are no hard-coded/default administrators.
   Keep `DATABASE_SITE_SECRET_KEY`/`SECRET_KEY` stable and private.
5. Deploy both Web Services. Sign in again using the existing Apps Database
   password. Old WWA Tools-only SQLite credentials are not an alternative login.
6. Administrators open `/admin/users` (also linked from the header). The page
   lists existing accounts and supports manual email/password creation, password
   resets, and activation/deactivation. It never displays stored password hashes.
   Other users cannot access this page, even by posting directly to its routes.
7. Check that the five accounts above appear as active. Existing accounts need
   no import. If an account is missing, add it manually with a new password and
   share it with that employee through an approved private channel. Email alone
   cannot restore a missing password; the deployment never invents/reset passwords.

The same account credentials work on both domains with separate login sessions.
Password changes revoke older sessions after the short user-cache TTL (60 seconds
by default); login and admin mutations bypass cached account records. S DB access
continues to depend on the existing S email allowlist, not on the administrator
role. `AUTH_REQUIRED=0` does not unlock the account administration UI.

For a trusted terminal with the service environment configured:

```bash
.venv/bin/flask --app database_site.app users check-team
.venv/bin/flask --app database_site.app users add employee@wildwildgroup.com
```

The add command prompts for a password without echoing it. `check-team` is
read-only and reports missing/inactive accounts; it does not overwrite hashes.

## S Database

Environment variables for the restricted S database:

- `DATABASE_SITE_S_SPREADSHEET_ID` - Google Sheet ID for the second team.
- `DATABASE_SITE_S_ALLOWED_EMAILS` - exact comma-separated emails that may access S DB, for example `lead@wildwildgroup.com,user@wildwildgroup.com`.
- `DATABASE_SITE_S_SERVICE_ACCOUNT_JSON` - optional separate service account JSON for S DB. If omitted, the site uses `DATABASE_SITE_SERVICE_ACCOUNT_JSON`.
- `DATABASE_SITE_S_APPS_SHEET` - optional Apps sheet name, defaults to `Apps`.
- `DATABASE_SITE_S_LOG_SHEET` - optional audit sheet name, defaults to `Checks`.

The existing main-site names `S_AVAILABILITY_DB_SPREADSHEET_ID` and
`S_LIVE_DB_ALLOWED_EMAILS` are also accepted as fallbacks. Access is enforced
on the server for every S DB read and write request.

Each Google Sheet must be shared as Editor with `client_email` from the service account JSON that serves it.

The site only writes columns `A:M` in the `Apps` sheet and audit entries in the `Checks` sheet. Disabling an app does not delete its history.
