# API

FastAPI + SQLAlchemy 2 (async) + PostgreSQL base with authentication (JWT) and RBAC
(users, roles, permissions). Domain entities are meant to be added on top.

## Prerequisites

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- Docker (for PostgreSQL)

## Setup

```bash
uv sync                     # install runtime + dev dependencies
cp .env.example .env        # then edit values if needed
```

Environment variables are validated at startup (`src/app/core/config.py`); the app
refuses to start if one is missing or invalid.

| Variable       | Description                                              | Default                |
| -------------- | -------------------------------------------------------- | ---------------------- |
| `DATABASE_URL` | **Required.** Must use the driver `postgresql+psycopg://` | –                      |
| `JWT_SECRET`   | **Required.** HS256 secret (≥16 chars) that signs the JWTs issued by `/auth/login`; the example placeholder is rejected in production | – |
| `ENVIRONMENT`  | `development`, `test` or `production`                    | `development`          |
| `LOG_LEVEL`    | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`          | `INFO`                 |
| `DB_ECHO`      | Log SQL statements                                       | `false`                |
| `APP_NAME`     | API title                                                | `API`                  |

Optional settings (defaults in `.env.example`):

| Variable                   | Description                                       | Default |
| -------------------------- | ------------------------------------------------- | ------- |
| `ACCESS_TOKEN_TTL_MINUTES` | Lifetime of the JWTs issued by `POST /auth/login` | `60`    |
| `CONFIRMATION_SECRET`      | Secret for refund confirmation tokens             | `JWT_SECRET` |
| `CONFIRMATION_TTL_MIN`     | Confirmation token lifetime (minutes)             | `10`    |
| `SLA_HOURS`                | Supervisor review SLA (hours)                     | `48`    |

`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` and `POSTGRES_PORT` configure the
Docker Postgres container, which uses the `pgvector/pgvector:pg17` image. The init
script enables the `vector` extension on a **fresh** volume; for an existing one run
`CREATE EXTENSION IF NOT EXISTS vector;` once in each database (or
`docker compose down -v`, which deletes local data).

## Authentication

Users sign in with email and password and receive a bearer token:

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "sup1@support.example", "password": "seed-password-1"}'
# {"access_token": "<jwt>", "token_type": "bearer", "expires_in": 3600}
```

Every other route except `/health` and `/healthz` requires `Authorization: Bearer <jwt>` signed with
`JWT_SECRET` (HS256), carrying only `sub` (the `User.id`), `iat` and `exp`; the lifetime
is `ACCESS_TOKEN_TTL_MINUTES`. Roles and permissions are looked up in the database on
every request (a token for an unknown user is `401`). A wrong password and an unknown
email both answer `401 {"detail": "Invalid credentials"}`.

Passwords (8–128 characters, used verbatim) are stored only as Argon2id hashes and are
never returned. A supervisor creates users with `POST /api/v1/users`
(`{"email", "name", "password"}`, always with the `customer` role); users can change their
own password with `PATCH /api/v1/users/{id}`. Users that existed before passwords were
introduced cannot sign in until a password is set (re-running `uv run seed` repairs the
seed users).

| Role         | Can                                                              |
| ------------ | ---------------------------------------------------------------- |
| `customer`   | read and update their own user                                   |
| `supervisor` | create users, read and update any user                           |

Roles map to permissions (`<resource>:<action>`, e.g. `user:read_any`) through the
`roles` / `permissions` tables (many-to-many on both sides: `user_roles`,
`role_permissions`); the catalog is in `src/app/core/permissions.py`. A caller without
the needed permission gets `403`; for customers, resources that belong to someone else
answer `404`, exactly like missing ones.

## Development

```bash
docker compose up -d db                         # start PostgreSQL (waits for healthcheck)
uv run alembic upgrade head                     # run migrations
uv run uvicorn app.main:app --reload --app-dir src
```

> **Windows:** `psycopg` async needs a `SelectorEventLoop`. `--reload` already uses
> it; without `--reload` add `--loop asyncio:SelectorEventLoop`. Docker/Linux needs
> nothing extra. Alembic and the tests already handle this.

- API: http://localhost:8000 (routes under `/api/v1`)
- Docs: http://localhost:8000/docs
- Health: http://localhost:8000/health (alias: `/healthz`)

## Seed data (development)

```bash
uv run seed                      # create the roles catalog, users, courses and the 13 refund cases, print bearer tokens (idempotent)
uv run seed --reset              # delete the seed data, then seed again
uv run seed --no-tokens          # do not print tokens (shared terminals)
uv run seed --token-ttl-hours 24 # token lifetime (default 168)
```

Every seed user has the development password `seed-password-1` (sign in with
`/auth/login`). Users: `supervisor-1` / `supervisor-2` (`sup1@support.example`,
`sup2@support.example`) and customers `ann@example.com` (owner of most cases) and
`bob@example.com`, plus three customers that carry refund history for their own case
(`double-request`, `high-frequency`, `abuse-limit`). Needs the migrations applied and
`JWT_SECRET` set. It refuses to run with `ENVIRONMENT=production`.
Ids are deterministic, so re-running changes nothing and the printed tokens stay valid.
`--reset` only deletes the seed users' data and the seed courses. The command prints one
token per user as `export NAME=<token>` lines and ready-to-copy `curl` examples (bash
syntax).

It also creates the 13 edge cases of the challenge (§9) as purchases and prints a table
`case -> purchase_id -> expected result`. Purchase dates are relative to `now()` and are
re-computed on every run, so re-run the seed if they drift (the "exact edge" case is day
30 minus 10 minutes, so it stays demonstrable for a few minutes only).

## Purchases (customer)

`GET /api/v1/me` (profile, roles, credit balance = SUM of the credit ledger),
`GET /api/v1/purchases` (own purchases, newest first; filters `status`,
`within_window`; cursor pagination with `cursor` and `limit`, 1-100) and
`GET /api/v1/purchases/{id}` (with progress and `refund_deadline`). Another customer's
purchase answers the same `404` as a missing one; a supervisor gets `403`.

## Production build

```bash
docker compose --profile production build api   # or: docker build -t api .
docker compose up -d db
docker compose --profile production run --rm api alembic upgrade head   # migrate first
docker compose --profile production up -d api
```

The image runs as a non-root user and does **not** run migrations automatically.
In production set `ENVIRONMENT=production`, a strong `POSTGRES_PASSWORD`, and a
`DATABASE_URL` pointing at your managed database; never commit `.env`.

## Migrations

```bash
uv run alembic revision --autogenerate -m "describe change"   # review the result!
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic check                                          # models vs. DB drift
```

## Tests

```bash
docker compose up -d db        # DB-backed tests need Postgres (they skip if unreachable)
uv run pytest
```

DB tests use the `customer_support_test` database (created automatically on first
`docker compose up`; override with `TEST_DATABASE_URL`). If your Postgres volume
already existed, create it once:
`docker compose exec db psql -U postgres -c "CREATE DATABASE customer_support_test"`.
The schema is dropped and recreated for every test.

## Code quality

```bash
uv run ruff check .            # lint (add --fix to autofix)
uv run ruff format .           # format code
uv run mypy                    # strict type checking
```

All at once:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

## Project structure

```
src/app/
  main.py          FastAPI app factory, error handlers, /health and /healthz
  api/             deps.py (dependency wiring, auth) + routes/ (HTTP layer)
  schemas/         Pydantic DTOs validating requests and shaping responses
  services/        auth, users, purchases, /me, refund events, RBAC catalog sync
  repositories/    database access, one per entity; scope.py = ownership scoping
  models/          SQLAlchemy ORM models, one file per aggregate
  domain/          pure business code (enums today; engine and state machine later)
  core/            config.py (validated env), database.py, security.py (JWT), pagination.py
  cli/             seed.py (`uv run seed`, development data)
migrations/        Alembic migrations
tests/             pytest suite
```

## Entities

`User (email unique; roles M2M)`, `Role`, and `Permission`, plus the refund domain:
`Course`, `Purchase`, `CourseProgress`, `RefundRequest`, `RefundEvent` (append-only),
`RefundInternalNote`, `RefundPayout`, `CreditLedgerEntry`, `EligibilityCheck`,
`OutboxEvent`, `ChatThread` and `PolicyDocument`. `refund_events` rejects UPDATE and
DELETE with a database trigger, and `refund_requests` has a partial unique index that
allows only one active request (`PENDING_APPROVAL`, `APPROVED`, `PROCESSING`) per
purchase.

### Listings

`GET /api/v1/users` is supervisor-only (customers get 403). It accepts `email`
(substring, case-insensitive), `role`, `limit` (1-200, default 100) and `offset`,
ordered by creation time.

## Specs

Implementation specs live in [docs/specs/](docs/specs/README.md).
