# Ledgerly / Credit Ledger Tracker

A multi-user ledger for money you've given out and taken, organized around
**People** rather than free-text transaction labels — with due dates,
automatic interest, auto-settling repayments, WhatsApp reminders, printable
receipts, a dashboard with charts, and monthly reports.

Each person who signs up gets their own private ledger. Accounts are isolated
at the database level: every query is filtered by the signed-in user, so
there is no code path that can return one account's data to another.

## Project structure

```text
udhaar-v2/
├── backend/
│   ├── main.py            FastAPI app, all API routes, serves the frontend
│   ├── models.py          SQLAlchemy models: Person → Transaction → Repayment
│   ├── database.py        Query layer (CRUD), all built on SQLAlchemy
│   ├── calculations.py    Interest math, due-date status, ledger building
│   ├── schemas.py         Pydantic request/response contracts
│   ├── config.py          Reads DATABASE_URL / SECRET_KEY / CORS_ORIGINS from the environment
│   ├── auth.py            Password hashing, JWT tokens, login throttling
│   ├── db_engine.py       SQLAlchemy engine + session setup
│   └── requirements.txt
├── alembic/                Database migrations (SQLAlchemy's migration tool)
│   ├── env.py
│   └── versions/0001_initial_schema.py
├── frontend/
│   ├── index.html          Sidebar + mobile bottom nav, 7 views
│   ├── styles.css
│   └── app.js              All API calls, rendering, charts (Chart.js via CDN)
├── tests/test_api.py
├── run.py                  `python run.py` — starts the app, no Docker
├── .env.example
└── alembic.ini
```

## Authentication

Sign-up is open to anyone who can reach the app (there's no invite system), so
**don't expose this publicly without understanding that**. Each account:

- Signs up with an email + password (bcrypt-hashed, never stored in plain text).
- Gets a login token (JWT) that the frontend stores in `localStorage` and sends
  as `Authorization: Bearer <token>` on every request. Tokens expire after 7
  days by default (`ACCESS_TOKEN_EXPIRE_MINUTES`).
- Can only ever see or change the people, transactions and repayments it owns —
  enforced in `database.py`'s queries, not just hidden in the UI.
- Has repeated failed logins throttled (5 attempts / 15 minutes per IP+email).
- Can delete itself, and everything it owns, from the sidebar's Danger Zone,
  after re-entering its password.

**If you already had data from before accounts existed**: the first person to
sign up automatically inherits it. After that, sign-up is first-come,
first-served for any new browser or device.

### SECRET_KEY

This signs login tokens — anyone who has it can forge a valid login for any
account. Generate one and set it as an environment variable before deploying
anywhere real:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Locally, if you don't set one, the app generates a random key on first run and
saves it to `backend/.secret_key` (git-ignored) so logins survive a restart.
That fallback is refused outright once `DATABASE_URL` points at a real
database — production deployments must set `SECRET_KEY` explicitly.

## Run it locally

**Fastest way (creates a virtual environment, installs everything, starts the app):**

```bash
./start.sh          # macOS / Linux / WSL / Git Bash
start.bat           # Windows (double-click or run in Command Prompt)
```

**Or manually:**

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
python run.py
```

Open **http://127.0.0.1:8000** — not by double-clicking `index.html`; the
frontend only works when FastAPI serves it, since that's what makes `/api/...`
resolve correctly. The launcher also works if called by path from another
directory (for example, `python ../run.py` while in `backend/`); it normalizes
the working directory so the frontend and the default database are still
served from this project. API docs live at **/docs**.

By default this uses a local SQLite file at `backend/ledgerly.db`,
created automatically. No setup needed.

### Run the tests

```bash
pip install httpx   # only needed for tests, not the app itself
python tests/test_api.py
```

## Database & migrations

The app talks to the database entirely through SQLAlchemy, so the same code
runs against SQLite (default, zero-config) or PostgreSQL — controlled purely
by the `DATABASE_URL` environment variable. See `.env.example`.

Schema changes are managed with **Alembic**. To apply migrations explicitly
(the app also auto-creates tables on first boot for convenience, so this is
mainly needed when you add new fields later):

```bash
alembic upgrade head
```

To generate a new migration after changing `backend/models.py`:

```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

## What's in this version

- **People**: a proper People section (`/api/people`) — name, phone, notes,
  aggregated totals (given, taken, paid, interest, outstanding), archive
  instead of hard delete. Adding a transaction can either pick an existing
  person or type a new name (it's created automatically).
- **Person detail page**: totals + full transaction history + quick actions
  (add transaction, WhatsApp reminder, edit, archive).
- **Dashboard**: Total Receivable / Payable / Outstanding / Interest cards,
  plus three charts (Outstanding by person, Money flow, Payment status) and
  a due-soon/overdue widget. Charts use Chart.js loaded from a CDN.
- **Due dates + grace period**: every transaction can have a due date and a
  grace period (default **2 days**, editable per transaction). Status is
  computed as 🔵 Upcoming / 🟡 Due Today / 🟠 Grace Period / 🔴 Overdue /
  🟢 Paid. Days overdue count from the *end* of the grace period.
- **Interest Calculator**: standalone what-if tool (amount, rate, loan date,
  due date, grace period, payment date) → principal, interest, total payable,
  phase and days overdue. Powered by `POST /api/calculator`, so it uses the
  exact same math as saved transactions.
- **In-app alerts**: a bell with a count and an Alerts panel on the Dashboard
  for items due in 2 days, due today, in grace period, or overdue. These are
  in-app only — no push/email/SMS (those need paid services or a background
  scheduler).
- **Repayments auto-settle**: when a transaction's balance reaches zero after
  a repayment, its status flips to Settled automatically. Partial payments
  don't trigger this.
- **WhatsApp reminders**: generated per-transaction or per-person, with a
  direct `wa.me` link when a phone number is on file, or copy-to-clipboard
  otherwise.
- **Printable receipts**: a "🧾 Receipt" button on any transaction opens the
  browser's print dialog with a clean, receipt-only layout — "Save as PDF"
  gives you a PDF with no extra dependencies or paid services involved.
- **Live interest preview** on the Add Entry form (client-side estimate —
  the authoritative number always comes from the backend on save).
- **Global search** (sidebar) across people and transactions.
- **Reports**: monthly Given / Taken / Repayments / Interest table + chart,
  computed from the same ledger logic as everything else.
- **Mobile bottom nav** (Home / People / + / Ledger / More) below 900px.
- CSV export and manual delete are unchanged from before.

## What's NOT in this version (deferred)

Being upfront about scope, per the size of the original wishlist:

- **CSV import** (mapping UI) — not built.
- **Activity/audit log** — not built.
- **Settings page** (currency, date format, notification preferences) — not
  built; currency is hardcoded to ₹ in `app.js` (`currencyFmt`).
- **Quick-add as a floating modal** — the mobile "+" currently navigates to
  the existing Add Entry page rather than opening an inline modal.
- Charts are drawn with Chart.js loaded from a public CDN
  (`cdnjs.cloudflare.com`) — fine for normal use, but note it as an external
  dependency if you need a fully offline/air-gapped deployment.

## Future deployment: Vercel + Render + PostgreSQL

The backend is already environment-driven, so getting there mainly means
setting a few environment variables — no code changes required:

```env
DATABASE_URL=postgresql://user:password@host:5432/dbname
SECRET_KEY=<64 random hex characters, see the Authentication section>
CORS_ORIGINS=https://your-frontend.vercel.app
```

(See `.env.example`.) A couple of things worth knowing before you do this:

- **Render (backend)**: set `DATABASE_URL` to your hosted Postgres connection
  string (Render, Neon, and Supabase all give you one directly). Set the
  start command to `alembic upgrade head && uvicorn backend.main:app --host
  0.0.0.0 --port $PORT`, so migrations run automatically on every deploy.
- **Vercel (frontend)**: since `frontend/` has no build step, point Vercel's
  "Root Directory" at `frontend/` and deploy as a static site. You'll then
  need to tell `app.js` where the backend actually lives — right now
  `const API_BASE = "/api"` assumes the frontend and backend share an origin,
  which is only true when FastAPI serves both (the current local setup).
  Splitting them onto Vercel + Render means changing that one line to your
  Render URL, e.g. `const API_BASE = "https://your-api.onrender.com/api"`,
  and adding CORS_ORIGINS on the backend to match your Vercel domain.
- This wasn't tested against a live Postgres instance in this environment
  (none was available) — the SQLAlchemy layer is dialect-agnostic and
  uncomment `psycopg2-binary` in `backend/requirements.txt` before deploying, and
  test against your actual Neon/Render Postgres database before relying on it.
- The login token lives in the browser's `localStorage`. That's simple and works
  across Vercel + Render, but it means any cross-site-scripting bug would expose
  it; the frontend escapes all user-entered text before rendering it.

## API reference

Full interactive docs at `/docs`. Summary:

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/signup` | Create an account (returns a login token) |
| POST | `/api/auth/login` | Sign in (returns a login token) |
| GET / DELETE | `/api/auth/me` | Current user / delete account (needs password) |
| GET | `/api/people` | List people with aggregated totals |
| POST | `/api/people` | Create a person |
| GET/PATCH/DELETE | `/api/people/{id}` | Fetch, update (incl. archive), or hard-delete a person |
| GET | `/api/transactions` | List transactions (ledger-computed), optional `?person_id=` |
| POST | `/api/transactions` | Create a transaction (`person_id` or `person_name`) |
| PATCH | `/api/transactions/{id}` | Update status (Active/Settled) |
| PATCH | `/api/transactions/{id}/due-date` | Update due date |
| DELETE | `/api/transactions/{id}` | Delete a transaction (cascades repayments) |
| POST | `/api/repayments` | Log a repayment (auto-settles if balance hits 0) |
| GET | `/api/repayments?transaction_id=` | List repayments |
| GET | `/api/reports/monthly` | Monthly summary + current outstanding |
| DELETE | `/api/reset` | Wipe all data |

All interest and due-date logic is centralized in `backend/calculations.py` —
the frontend never recomputes it, only displays it.
