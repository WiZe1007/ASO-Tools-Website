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
- Start command: `gunicorn database_site.app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120`
- Health check path: `/health`

Required environment variables for the WWA database:

- `DATABASE_SITE_SPREADSHEET_ID` - Google Sheet ID.
- `DATABASE_SITE_SERVICE_ACCOUNT_JSON` - the complete service account JSON.
- `DATABASE_SITE_SECRET_KEY` - a stable random string, at least 32 characters.
- `DATABASE_SITE_ALLOWED_EMAIL_DOMAIN` - `@wildwildgroup.com`.
- `DATABASE_SITE_AUTH_DB` - `/var/data/database-site-users.db` when a Render Disk is mounted at `/var/data`.
- `DATABASE_SITE_SECURE_COOKIES` - `1` on Render.

The WWA database is available to every registered user whose email ends with
`@wildwildgroup.com`.

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
