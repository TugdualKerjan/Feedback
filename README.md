# Marc — anonymous feedback on any page

Paste a link, share the generated session URL, and Marc will overlay your visitors' highlights with a sidebar of comments. No accounts, no JS bundlers, just FastAPI, a lightweight proxy, and some careful DOM surgery.

## Getting started

1. `python -m venv .venv && source .venv/bin/activate`
2. `pip install -e .`
3. `uvicorn main:app --reload`
4. Open the landing page to create a session, then visit `/r/<session-id>` to view the proxied site with Marc injected.

## Environment variables

| Name | Default | Purpose |
| --- | --- | --- |
| `BASE_URL` | `https://feedback.tugdual.fr` | The publicly served base URL that prefixes `/r/<session-id>` links and populates the overlay script.
| `DB_PATH` | `marc.db` | Path to the SQLite database storing sessions and annotations.
| `SESSION_RATE_LIMIT` | `5` | Number of `/session` creations allowed per `SESSION_RATE_WINDOW` seconds per IP.
| `SESSION_RATE_WINDOW` | `60` | Time window (in seconds) used for rate limiting session creation.
| `MARC_DEBUG` | `false` | When true, the injected overlay emits debug logs (only show this in development).

## Architecture overview

- `config.py` centralizes runtime knobs and exports `MARC_DEBUG` for the overlay helper.
- `db.py` opens the database with SQLite WAL mode, enables foreign keys, and exposes `get_db` + `init_db`.
- `api.py` hosts the landing page, the `/session` endpoint (with SSRF checks and rate limiting), and the annotations API.
- `proxy.py` rewrites asset URLs, injects a `<base>` element, strips CSP meta tags, and appends the overlay payload when serving proxied pages.
- `overlay.py` reads `templates/overlay.html`/`landing.html`, replaces the `__SESSION_ID__`, `__SERVER_BASE__`, `__TARGET_URL__`, and `__MARC_DEBUG__` tokens, and exports both the script and landing markup.
- `templates/` contains the static HTML used for the overlay badge and the landing site so designers can edit the markup without touching Python.

## Notes

- The proxy rewrites CSS `url(...)`, `@import`, and `srcset` references so static assets continue to be fetched through Marc.
- Annotations remain tied to the `root_url`, so the overlay refuses to POST comments for URLs outside the original session root.
- SQL statements rely on WAL mode and `PRAGMA foreign_keys = ON` for resiliency; no migrations are needed for the current schema.
