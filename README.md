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

- `APPMAGIC_TOKEN`
- `APPMAGIC_BEARER_TOKEN`
- `APPMAGIC_COOKIE`

Without App Magic auth, the app still works, but exact App Magic country/download data may be unavailable for some apps.
