# WWA ASO Tools

Flask web app for ASO checks:

- GEO rating checker for Google Play / App Store
- install availability checker by country
- Google Play GEO link generator
- App Overview page with public Sensor Tower data
- optional App Magic data-countries integration for download shares
- Telegram Availability monitor backed by Google Sheets

## Local Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Open the URL printed in the terminal.

## Render Deploy

Use a Web Service connected to this GitHub repository.

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
gunicorn app:app --bind 0.0.0.0:$PORT
```

The same command is also stored in `Procfile`.

## Optional Environment Variables

These are optional. Do not commit real values.

- `APPMAGIC_BEARER_TOKEN` - required for fully automatic App Magic mode on Render. Add the App Magic `Authorization: Bearer ...` token here once, and users will not need to paste anything in the website.
- `APPMAGIC_TOKEN` - alternative name for the same Bearer token.
- `APPMAGIC_COOKIE` - fallback if you need cookie-based App Magic access.

Without App Magic auth, the app still works, but exact App Magic country/download data may be unavailable for some apps. On hosting, browser auto-import is intentionally local-only, so use `APPMAGIC_BEARER_TOKEN` for the shared employee site.

For Render automatic App Magic mode:

1. Open Render dashboard.
2. Go to the web service.
3. Open `Environment`.
4. Add `APPMAGIC_BEARER_TOKEN`.
5. Paste the App Magic Network `Authorization: Bearer ...` value.
6. Redeploy the service.

After redeploy, App Magic mode is automatic for everyone who opens the hosted site.

## Telegram Availability Monitor

This feature uses Google Sheets as the shared company database and Telegram as the notification channel.

### Google Sheet Structure

Create a Google Spreadsheet and add app links to the `Apps` sheet. The app will create/update headers automatically.

Required columns:

```text
enabled | status | app_url | app_id | app_name | owner | notes | last_checked_at | last_live_at | last_open_countries | last_closed_countries | last_closed_count | last_error
```

Use:

- `enabled`: `TRUE` / `FALSE`
- `status`: use `watch` for apps that are not live yet; use `live` for apps that are already live and should only be monitored for future country closures
- `app_url`: Google Play link, for example `https://play.google.com/store/apps/details?id=com.example.app`
- `app_name`, `owner`, `notes`: optional human fields

The bot also writes events to the `Checks` sheet.

### Google Service Account

1. Create a Google Cloud service account.
2. Enable Google Sheets API for the project.
3. Create a JSON key for the service account.
4. Share the Google Spreadsheet with the service account email as `Editor`.
5. Add the JSON to Render as `GOOGLE_SERVICE_ACCOUNT_JSON`.

For Render env vars, add:

```text
AVAILABILITY_DB_SPREADSHEET_ID=<spreadsheet id from the Google Sheet URL>
GOOGLE_SERVICE_ACCOUNT_JSON=<full service account JSON>
TELEGRAM_BOT_TOKEN=<token from @BotFather>
TELEGRAM_CHAT_ID=<company chat/channel id>
```

Optional:

```text
AVAILABILITY_DB_APPS_SHEET=Apps
AVAILABILITY_DB_LOG_SHEET=Checks
AVAILABILITY_CHECK_LIMIT=200
BOT_TIMEZONE=Europe/Kiev
BOT_CHECK_HOURS=9,15,21
TELEGRAM_ALLOWED_CHAT_IDS=<optional comma-separated chat ids for commands>
AVAILABILITY_TASK_SECRET=<only needed if you also use the HTTP task endpoint>
```

### How Notifications Work

The monitor checks Google Play availability with the same country list and logic as the `/availability` page.

It sends Telegram messages when:

- an app first becomes live in at least one country;
- a country that was previously open becomes closed.

For already live apps, set `status=live` before the first bot run. Then the first run creates a baseline without sending a "new live" notification.

### Separate Telegram Bot

The repository includes a separate Telegram bot process in `telegram_bot.py`.

Create the bot in Telegram:

1. Open `@BotFather`.
2. Send `/newbot`.
3. Copy the bot token to `TELEGRAM_BOT_TOKEN`.
4. Add the bot to the company chat/group.
5. Send `/chatid` to the bot in that chat.
6. Put that chat id into `TELEGRAM_CHAT_ID`.

Render setup:

1. Create a new Render service from the same GitHub repository.
2. Choose **Background Worker**.
3. Use the same environment variables as the web service.

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
python telegram_bot.py
```

The bot keeps running separately from the website. It supports:

```text
/start  - help
/chatid - show current chat id
/status - show database status
/check  - run a live check now
/dryrun - check without writing to Google Sheets and without Telegram alerts
```

Automatic checks are controlled by:

```text
BOT_TIMEZONE=Europe/Kiev
BOT_CHECK_HOURS=9,15,21
```

### Cron Alternative

If you do not want a permanently running bot worker, you can run only the scheduled check as a Render Cron Job.

Command:

```bash
python app.py bot-check
```

Schedule it 3 times per day, for example:

```text
0 9,15,21 * * *
```

Manual dry run locally or in Render Shell:

```bash
python app.py bot-check --dry-run
```

Dry run reads the sheet and checks apps, but does not update the sheet and does not send Telegram messages.

You can also trigger the hosted web service endpoint:

```bash
curl -X POST "https://YOUR-RENDER-URL/tasks/check-availability?secret=AVAILABILITY_TASK_SECRET"
```
