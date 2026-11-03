# Stamp Card — Backend

A digital loyalty stamp card for small shops. A customer scans a QR code, fills in a short form and adds a pass to Google Wallet. The cashier scans the pass to add a stamp, and when the card is full the same scan redeems the reward.

This repo is the FastAPI backend. The two web apps are separate repos:

- [stampcard-frontend](https://github.com/Kontogiannis-dev/stampcard-frontend): customer onboarding and "Add to Wallet" (React + Vite)
- [stampcard-owner](https://github.com/Kontogiannis-dev/stampcard-owner): staff app with login, customer list and QR scanner (React + Vite)

## How it works

1. The shop's QR code opens `/join/{shop_id}`, which redirects the customer to the onboarding form.
2. The customer submits name and phone. The backend creates a card and returns an identifier.
3. The customer adds the pass to Google Wallet. The pass QR contains only that identifier.
4. The cashier logs in with a PIN and scans the pass. `POST /owner/{shop_id}/stamp` adds a stamp, or redeems the reward if the card is already full, then updates the wallet pass.

## Quick start (Docker)

```bash
git clone https://github.com/Kontogiannis-dev/stampcard-backend.git
cd stampcard-backend
cp .env.example .env      # then edit the values
docker compose up --build
```

Open http://localhost:8000/docs for the interactive API docs.

This starts the backend and a Postgres database together. Tables are created automatically by Alembic on startup, and a demo shop is seeded using the `OWNER_PIN` from your `.env`.

Without Google credentials, the wallet runs in **mock mode**: everything works, and the pass link is a placeholder.

To reset all data: `docker compose down -v`.

## Running without Docker

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

With no `DATABASE_URL` set, it uses a local SQLite file.

## Configuration

See `.env.example`.

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Signs owner login tokens (JWT) |
| `OWNER_PIN` | Owner PIN, stored as a bcrypt hash |
| `HOST_IP` | Host the frontends use to reach the backend (redirects, CORS) |
| `DATABASE_URL` | Optional. Postgres URL, defaults to local SQLite |
| `GOOGLE_WALLET_ISSUER_ID` | Optional. Enables real Google Wallet passes (also needs `service-account.json`) |

## Design decisions

- **Login rate limit:** `/owner/login` allows 5 attempts per minute per client, since a short PIN can otherwise be brute-forced. The client key reads `CF-Connecting-IP` so it also works behind a Cloudflare Tunnel.
- **Hashed PIN:** the owner PIN is stored as a salted bcrypt hash, never in plain text.
- **Double-scan protection:** a card can't be stamped twice within 10 seconds. A refused scan returns HTTP 429 and leaves the card untouched.
- **Fast scans:** the Google Wallet update runs as a background task after the response is sent. Locally, a stamp request returns in roughly 250 ms. Wallet failures are logged with the customer identifier instead of failing the scan.
- **Migrations:** the schema is managed with Alembic, so columns can be added to a database that already holds data.
- **Smart scan:** one endpoint handles both stamping and redeeming, based on the card's state.
- **Portable config:** the database URL comes from the environment, so the same code runs on SQLite and Postgres.

## Tech stack

FastAPI, SQLAlchemy, Alembic, PostgreSQL (SQLite for quick local runs), PyJWT, bcrypt, slowapi, Google Wallet API, Docker Compose.

## Known limitations

- A single shop is seeded automatically. Multi-shop signup isn't built.
- Owner auth is one shared PIN per shop, not individual accounts.
- No Apple Wallet yet (needs a paid Apple Developer account).
- The rate limiter and cooldown are simple and in-memory or per-row, which is fine for one container.