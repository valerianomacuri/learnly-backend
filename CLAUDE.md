# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

FastAPI + async SQLAlchemy 2 + PostgreSQL API template providing authentication (JWT + Argon2id passwords) and RBAC (users, roles, permissions) only; domain entities are added on top. Python 3.13, managed with `uv`. See `README.md` for env vars and production build steps.

## Commands

```bash
uv sync                                          # install deps
cp .env.example .env                             # DATABASE_URL and JWT_SECRET are required
docker compose up -d db                          # PostgreSQL + pgvector (also creates customer_support_test DB)
uv run alembic upgrade head                      # migrate
uv run seed                                      # dev data + printed JWTs (idempotent; --reset, --no-tokens); refuses ENVIRONMENT=production
uv run uvicorn app.main:app --reload --app-dir src

uv run pytest                                    # all tests
uv run pytest tests/test_flow.py::test_name      # single test
uv run ruff check . --fix && uv run ruff format .
uv run mypy                                      # strict mode

uv run alembic revision --autogenerate -m "msg"  # review output; migrations/versions is excluded from ruff
uv run alembic check                             # models vs. migrations drift
```

- DB-backed tests need Postgres and **skip** (not fail) if it is unreachable. The `session_maker`/`client` fixtures in `tests/conftest.py` drop and recreate the schema with `Base.metadata.create_all` for every test, so tests don't exercise Alembic migrations; override the DB with `TEST_DATABASE_URL`. Other fixtures: `make_user(RoleName, extra_roles=, email=)` inserts a user with its roles and a pre-computed password hash (Argon2 is slow) and returns `Actor(id, headers, email, password)`, there are no domain fixtures.
- Windows: psycopg async needs `SelectorEventLoop`. Without `--reload`, pass `--loop asyncio:SelectorEventLoop` to uvicorn. The tests already supply it via `pytest_asyncio_loop_factories`.
- Settings are validated at import time (`app = create_app()` in `main.py`), so importing `app.main` without a valid `DATABASE_URL` and `JWT_SECRET` fails. `conftest.py` sets the env vars before importing app modules.

## Architecture

Layered, with a strict one-way dependency: `api/routes` → `services` → `repositories` → `models`. `schemas/` (Pydantic DTOs) are used at the route and service boundary.

- **Wiring**: `api/deps.py` builds a service per request from the request-scoped `AsyncSession`. Each service constructs the repositories it needs from that same session, so all work in a request shares one transaction.
- **Transactions**: `core/database.get_session` commits once at the end of a successful request and rolls back on any exception. Repositories only `flush` (see `BaseRepository.add`) and never commit. Services mutate ORM objects and flush; don't commit inside them.
- **Errors**: services raise `NotFoundError` / `ConflictError` / `ForbiddenError` (`services/errors.py`). `main.py` maps them to 404 / 409 / 403 JSON, so routes don't catch them. Add new domain errors there and register a handler. Raising rolls the request transaction back, so an outcome that must persist (e.g. a failed login) is **returned**, not raised (see `AuthService`).
- **Auth / RBAC**: every route except `/health` needs a JWT bearer (`core/security.py`) whose only claims are `sub` (the `User.id`), `iat` and `exp`. Roles and permissions are read from the database on every request (`UserRepository.get_principal`: `users` ↔ `user_roles` ↔ `roles` ↔ `role_permissions` ↔ `permissions`, both many-to-many), so changing a role takes effect without a new token; an unknown user is 401. The catalog (roles `customer` / `supervisor`, permissions `<resource>:<action>` such as `user:read_any`) lives in `core/permissions.py` and is written to the tables by `services/rbac.ensure_rbac` (seed and tests). Routes receive a `Principal` through `PrincipalDep` or the `requires(Perm.X)` guard (`api/deps.py`) and pass it to services; services re-check with `require_permission` / `require_any_permission` (`services/authz.py`). `POST /api/v1/auth/login` (`api/routes/auth.py`, `services/auth_service.py`) exchanges email + password for a token; passwords are Argon2id hashes (`core/passwords.py`, `users.password_hash`), never returned (`UserRead` has no password field), and a wrong password or unknown email give the same 401 (dummy-hash verify for timing) and a failure that is *returned, not raised*. Password fields are `SecretStr` and are not whitespace-stripped (`schemas/user.py`, `schemas/auth.py` turn `str_strip_whitespace` off). A pre-password user holds `UNUSABLE_HASH` (`!`) and cannot sign in. `create_access_token` is also used by the seed and tests.
- **Owner scoping**: there is no tenancy. Resources belong to a `User`. `repositories/scope.restrict(stmt, principal, Perm.X_READ_ANY)` lets a caller holding the `*_any` permission see every row and limits everyone else to rows they own. Out-of-scope rows behave like missing rows (404). Never fetch by id alone in a service that serves a caller; use `get_scoped`.
- **Repositories**: subclass `BaseRepository[Model]` and set `model`. It provides get/list/add/delete, and entity-specific queries go in the subclass.
- **Models**: `models/base.py` holds the constraint naming convention (needed for Alembic autogenerate), `IdMixin` (UUID pk), `TimestampMixin`, and `str_enum`, which stores enums as VARCHAR + CHECK rather than native PG enums. Use `str_enum` for new status columns.
- **Entities**: `User` (unique email; roles via `user_roles`), `Role`, and `Permission`.
- **Domain packages**: `domain/` holds pure business logic and must not import SQLAlchemy, FastAPI or `app.services` (enforced by `tests/unit/test_architecture.py`). `agent/`, `rag/` and `workers/` are reserved and created in their own spec.
- **Audit**: `refund_events` is the only audit trail; don't add other audit tables.
- **Specs**: feature work is specified in `docs/specs/NN-*.md` (index in `docs/specs/README.md`); implement them in order and tick their tasks.
- **Settings**: `core/config.py` (`DATABASE_URL`, `JWT_SECRET`, `ACCESS_TOKEN_TTL_MINUTES`, `CONFIRMATION_SECRET` (defaults to `JWT_SECRET`), `CONFIRMATION_TTL_MIN`, `SLA_HOURS`, ...). Policy thresholds are not settings; they come from the policy front-matter.
- **Makefile**: `make check` runs lint, typecheck, tests and evals (needs `make`; on Windows without it run the `uv run` commands above).
