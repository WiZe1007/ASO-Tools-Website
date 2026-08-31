# Security Audit: WWA Tools and WWA Apps Database

Date: 1 September 2026

Scope:

- `app.py` (WWA Tools)
- `database_site/app.py` (WWA Apps Database)
- shared account and session code in `account_access.py`
- Telegram application-card media loading
- templates, routes, dependencies, and deployment-related configuration

The audit was performed as a white-box review and controlled local penetration
test. No destructive or high-volume requests were sent to the production Render
services.

## Result

The audit found several practical weaknesses. They were fixed and covered by
regression tests. No known dependency vulnerability and no medium/high Bandit
finding remains after the changes.

No audit can prove that a public site is impossible to compromise. The current
code closes the identified application-level paths, while account security still
depends on private Render secrets, strong passwords, and timely dependency updates.

## Findings Fixed

### 1. Login brute force

Before the fix, both login forms allowed unlimited password attempts.

Now repeated failures are limited per email and remote address. Six failures in a
15-minute window return HTTP `429` with `Retry-After`. A successful login clears
the corresponding failure history. The limiter is bounded to prevent an attacker
from growing its memory without limit.

### 2. Account enumeration through response timing

A missing or inactive account previously returned before running the expensive
password-hash check. An attacker could compare many response times to discover
registered corporate email addresses.

Missing, inactive, and malformed account rows now execute a dummy scrypt check,
so the login response does not reveal whether the account exists.

### 3. Authentication accidentally disabled on Render

`AUTH_REQUIRED=0` and `DATABASE_SITE_AUTH_REQUIRED=0` were useful for local
development but too dangerous as hosted configuration switches.

On Render, authentication is now always enabled and Secure cookies are always
used, even if a disabling environment value is present. The bypass remains
available only outside a detected hosted runtime.

### 4. Local-only endpoint exposure behind a reverse proxy

Several AppMagic import and process-control endpoints trusted a loopback remote
address. A hosted reverse proxy can make an external request appear to originate
from a local address.

Local AppMagic credential persistence, automatic import, `/shutdown`, `/exit`, and
the no-secret task fallback now require both a loopback request and a non-hosted
runtime. Render requests cannot use those local features.

### 5. Cross-origin requests and logout CSRF

Unsafe browser requests now validate `Sec-Fetch-Site`, `Origin`, and `Referer`.
Requests from sibling Render subdomains are not treated as same-origin.

Logout was changed from `GET` to `POST` and requires the session CSRF token. A
third-party page can no longer log a user out by embedding a link or image.
`Origin: null` is accepted only when browser metadata independently confirms a
same-origin request.

### 6. Scheduled-task secret exposure

`/tasks/check-availability` previously accepted its secret in the query string,
where it could leak into logs, history, and monitoring systems. It also accepted
`GET` and used a normal string comparison.

The endpoint is now `POST` only. It accepts the secret through
`X-Task-Secret` or a JSON body and compares it in constant time. Query-string
secrets are rejected. On Render, a missing configured secret fails closed.

### 7. Deceptive and malformed store URLs

Store detection previously used substring checks. Inputs such as
`play.google.com@attacker.example`, attacker domains containing an official name,
malformed ports, control characters, or non-HTTP schemes could be misclassified.

URL parsing now requires HTTPS, an official host, no embedded credentials, the
default HTTPS port, and a valid package identifier. URLs read from Google Sheets
are rebuilt from the validated package name before being returned to a browser.

### 8. Telegram card SSRF and resource exhaustion

Remote icon and screenshot URLs were downloaded without an allowlist, redirect
validation, streaming size limit, or decoded-pixel limit.

Card media now:

- permits only HTTPS Googleusercontent hosts;
- rejects credentials and nonstandard ports;
- validates every redirect and cannot redirect to localhost/private hosts;
- streams at most 8 MB;
- rejects non-image content and images above the pixel limit.

### 9. Oversized request bodies

Both Flask applications now reject request bodies above 64 KB with HTTP `413`.
This covers the small JSON and form payloads used by the sites and reduces a cheap
memory-exhaustion path.

### 10. Browser security headers and cookies

Both applications now consistently emit:

- Content Security Policy;
- `X-Frame-Options: DENY`;
- `X-Content-Type-Options: nosniff`;
- `Referrer-Policy: no-referrer`;
- restrictive `Permissions-Policy`;
- `Cross-Origin-Opener-Policy: same-origin`;
- HSTS in hosted mode;
- `Cache-Control: no-store` on non-static responses.

Session cookies are separate for the two sites and use `Secure`, `HttpOnly`, and
`SameSite=Lax` on Render. Sessions remain bound to the site's account realm and
the current password hash, so changing a password invalidates old sessions.

### 11. Vulnerable image dependency

The accepted Pillow range included a release reported by `pip-audit` with known
security advisories. The project now pins Pillow to the audited `12.3.0` release.

### 12. Inline-script CSP bypass

The initial CSP still allowed `script-src 'unsafe-inline'`, which would have made
the policy much less useful if an HTML injection were introduced later.

Every response now gets a cryptographically random CSP nonce. All seven inline
application scripts receive that nonce, and `script-src` accepts only same-origin
scripts or scripts carrying the response-specific nonce. Inline event handlers
remain disallowed.

### 13. Spreadsheet-formula injection in CSV export

CSV cells beginning with `=`, `+`, `-`, or `@` can be interpreted as formulas by
Excel or another spreadsheet program even when the CSV field is quoted.

The browser export now prefixes those values with an apostrophe before quoting
them. User-controlled labels remain text when the downloaded CSV is opened.

### 14. Resource-heavy endpoint abuse

An authenticated user could repeatedly call the Google Play, Sensor Tower, and
availability checks and consume the service's worker capacity.

Those endpoints now share a per-user and remote-address sliding-window limit.
The default is 12 requests per minute and can be adjusted with
`WWA_HEAVY_REQUESTS_PER_MINUTE`. Alternating between tools does not bypass it.

### 15. Dependency drift

Direct production dependencies are pinned to audited versions. A weekly
Dependabot configuration was added so version and security updates are proposed
instead of silently changing during an unrelated Render deploy.

## Verification

The final verification includes:

- complete Python unit and integration suite;
- authentication, authorization, CSRF, session invalidation, and account-scope tests;
- deceptive URL, media redirect, oversized download, and request-body tests;
- live local Gunicorn tests for both applications;
- browser rendering and form submission in Chromium;
- `pip-audit`, `pip check`, `py_compile`, Bandit, and tracked-file secret scans.

Production-like local checks confirmed:

- only `/health` and `/login` are anonymously available;
- private pages redirect to login;
- private APIs return `401`;
- local process-control endpoints remain unavailable;
- the scheduled task returns `403` without its header secret;
- `GET /logout` returns `405` and `POST /logout` without CSRF returns `403`;
- Secure cookie attributes, HSTS, CSP, and no-store headers are present.

## Residual Risks and Required Operations

### Rate-limit storage

The login and expensive-request limiters are intentionally lightweight and stored
in each Python process. They reset after a restart and are not shared between
multiple workers. For stronger protection against distributed attacks, add a
Render/Cloudflare rate limit or move counters to a shared store such as Redis.

### Multi-factor authentication

The sites still use email and password without MFA. Google Workspace SSO or an
identity-aware proxy with MFA would provide the largest additional improvement,
especially for administrators.

### Content Security Policy

Inline scripts are protected with response-specific nonces. The templates still
contain inline style blocks and dynamic style attributes, so `style-src` retains
`'unsafe-inline'`. Moving those remaining styles to classes would further harden
CSS injection, but it does not permit arbitrary JavaScript execution.

### Secret rotation

Earlier Render screenshots displayed Telegram bot tokens and an AppMagic token.
Those values should be treated as exposed and rotated. If any Google service
account private-key material was visible or shared, create a new key and revoke
the old one as well.

### Render environment checklist

Keep these values long, random, private, and stable:

- WWA Tools: `SECRET_KEY`
- Apps Database: `DATABASE_SITE_SECRET_KEY`
- scheduled HTTP task: `AVAILABILITY_TASK_SECRET`

The scheduler must send `AVAILABILITY_TASK_SECRET` in the `X-Task-Secret` header,
not in the URL. Keep `AUTH_ADMIN_EMAILS` limited to the smallest necessary set.
