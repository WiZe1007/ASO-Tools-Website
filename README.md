# WWA ASO Tools

Flask web app for ASO checks:

- GEO rating checker for Google Play / App Store
- install availability checker by country
- Google Play GEO link generator
- App Overview page with public Sensor Tower data
- optional App Magic data-countries integration for download shares

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
