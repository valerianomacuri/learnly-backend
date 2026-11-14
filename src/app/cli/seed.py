"""Development seed: `uv run seed` (idempotent; `--reset` re-creates it).

Creates the roles/permissions catalog and a few users (supervisors and customers).
Every seed user gets the same development password (`SEED_PASSWORD`) and the
command prints a bearer token per user. Development only: it refuses to run when
ENVIRONMENT=production.
"""

import argparse
import asyncio
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.core.passwords import UNUSABLE_HASH, hash_password
from app.core.permissions import RoleName
from app.core.security import create_access_token
from app.models.audit_event import AuditEvent
from app.models.rbac import Role
from app.models.user import User
from app.services.rbac import ensure_rbac

# Fixed namespace: ids are derived from names, so re-runs find the same rows and
# the printed tokens stay valid across runs.
_NAMESPACE = uuid.UUID("5eed0000-0000-4000-8000-000000000000")
DEFAULT_TOKEN_TTL_HOURS = 168
# Development only (the seed refuses to run in production).
SEED_PASSWORD = "seed-password-1"


def sid(name: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, name)


def user_id(key: str) -> uuid.UUID:
    return sid(f"user:{key}")


@dataclass(frozen=True, slots=True)
class UserSpec:
    key: str
    name: str
    email: str
    role: RoleName


SUPERVISORS = (
    UserSpec(
        "supervisor-1", "Supervisor One", "sup1@support.example", RoleName.SUPERVISOR
    ),
    UserSpec(
        "supervisor-2", "Supervisor Two", "sup2@support.example", RoleName.SUPERVISOR
    ),
)
CUSTOMERS = (
    UserSpec("ann", "Ann Rivera", "ann@example.com", RoleName.CUSTOMER),
    UserSpec("bob", "Bob Stone", "bob@example.com", RoleName.CUSTOMER),
)
USERS = SUPERVISORS + CUSTOMERS


@dataclass(frozen=True, slots=True)
class TokenInfo:
    label: str
    role: RoleName
    token: str


@dataclass(slots=True)
class SeedReport:
    tokens: list[TokenInfo] = field(default_factory=list)
    users: dict[str, uuid.UUID] = field(default_factory=dict)


async def _ensure_user(
    session: AsyncSession,
    spec: UserSpec,
    roles: dict[RoleName, Role],
    password_hash: str,
) -> uuid.UUID:
    uid = user_id(spec.key)
    user = await session.get(User, uid)
    if user is None:
        session.add(
            User(
                id=uid,
                name=spec.name,
                email=spec.email,
                password_hash=password_hash,
                roles=[roles[spec.role]],
            )
        )
    else:  # keep older seed rows usable: right email, and a password if missing
        user.email = spec.email
        user.name = spec.name
        if user.password_hash == UNUSABLE_HASH:
            user.password_hash = password_hash
    return uid


async def reset(session: AsyncSession) -> None:
    """Delete only the rows that belong to the seed users."""
    users = [user_id(spec.key) for spec in USERS]
    await session.execute(delete(User).where(User.id.in_(users)))  # cascades user_roles
    await session.execute(
        delete(AuditEvent).where(AuditEvent.actor.in_([str(uid) for uid in users]))
    )
    await session.flush()


async def seed(session: AsyncSession, *, ttl: timedelta) -> SeedReport:
    """Create (or complete) the seed data; safe to run repeatedly."""
    report = SeedReport()
    roles = await ensure_rbac(session)
    password_hash = hash_password(SEED_PASSWORD)  # one hash shared by every seed user
    for spec in USERS:
        report.users[spec.key] = await _ensure_user(session, spec, roles, password_hash)
    await session.flush()

    for spec in USERS:
        report.tokens.append(
            TokenInfo(
                spec.key,
                spec.role,
                create_access_token(user_id=report.users[spec.key], expires_in=ttl),
            )
        )
    return report


def render(report: SeedReport, *, show_tokens: bool, base_url: str) -> str:
    out: list[str] = ["", "Seed data ready", "", f"Users (password: {SEED_PASSWORD})"]
    for spec in USERS:
        out.append(
            f"  {spec.key:<13} {spec.role.value:<11} {spec.email:<24} "
            f"{report.users[spec.key]}"
        )

    sup1 = "$SUPERVISOR_1" if show_tokens else "<supervisor-1 token>"
    if show_tokens:
        out += ["", "Tokens (bash: copy these lines; valid until they expire)"]
        for t in report.tokens:
            out.append(f"  export {t.label.upper().replace('-', '_')}={t.token}")
    out += ["", "Try it"]
    out.append(
        f'  curl -X POST -H "Content-Type: application/json" '
        f'-d \'{{"email":"sup1@support.example","password":"{SEED_PASSWORD}"}}\' '
        f'"{base_url}/api/v1/auth/login"   # -> {{"access_token": ...}}'
    )
    out.append(f'  curl -H "Authorization: Bearer {sup1}" "{base_url}/api/v1/users"')
    out.append("")
    return "\n".join(out)


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="seed", description="Seed the development database (idempotent)."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete the seed users' data first, then seed again",
    )
    parser.add_argument(
        "--token-ttl-hours",
        type=int,
        default=DEFAULT_TOKEN_TTL_HOURS,
        help=f"lifetime of the printed tokens (default {DEFAULT_TOKEN_TTL_HOURS})",
    )
    parser.add_argument(
        "--no-tokens", action="store_true", help="do not print bearer tokens"
    )
    parser.add_argument(
        "--base-url", default="http://localhost:8000", help="API URL used in examples"
    )
    return parser.parse_args(argv)


async def _run(settings: Settings, args: argparse.Namespace) -> SeedReport:
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            try:
                if args.reset:
                    await reset(session)
                report = await seed(session, ttl=timedelta(hours=args.token_ttl_hours))
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        return report
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse(argv)
    if args.token_ttl_hours <= 0:
        print("error: --token-ttl-hours must be positive", file=sys.stderr)
        return 2
    settings = get_settings()
    if settings.environment == "production":
        print("error: refusing to seed with ENVIRONMENT=production", file=sys.stderr)
        return 1
    try:
        # psycopg async can't use Windows' default ProactorEventLoop.
        report = asyncio.run(
            _run(settings, args), loop_factory=asyncio.SelectorEventLoop
        )
    except Exception as exc:
        print(f"error: seeding failed: {exc}", file=sys.stderr)
        return 1
    print(render(report, show_tokens=not args.no_tokens, base_url=args.base_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
