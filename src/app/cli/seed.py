"""Development seed: `uv run seed` (idempotent; `--reset` re-creates it).

Creates the roles/permissions catalog, a few users (supervisors and customers), a
small course catalog and the 13 edge cases of the challenge (§9) as purchases.
Every date is relative to `now()`, so each run re-dates the purchases and the
cases stay valid. Every seed user gets the same development password
(`SEED_PASSWORD`) and the command prints a bearer token per user plus a table
`case -> purchase_id -> expected result`. Development only: it refuses to run
when ENVIRONMENT=production.
"""

import argparse
import asyncio
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.core.passwords import UNUSABLE_HASH, hash_password
from app.core.permissions import RoleName
from app.core.security import create_access_token
from app.domain.enums import (
    ActorType,
    Channel,
    PaymentMethod,
    PurchaseStatus,
    PurchaseType,
    ReasonCategory,
    RefundMethod,
    RefundStatus,
)
from app.models.base import Base
from app.models.chat import ChatThread
from app.models.course import Course
from app.models.eligibility import EligibilityCheck
from app.models.purchase import CourseProgress, Purchase
from app.models.rbac import Role
from app.models.refund import (
    APPEND_ONLY_TRIGGER,
    CreditLedgerEntry,
    RefundEvent,
    RefundInternalNote,
    RefundPayout,
    RefundRequest,
)
from app.models.user import User
from app.policy.load import default_policies_dir, load_policies
from app.services.rbac import ensure_rbac
from app.services.refund_event_service import EventActor, RefundEventService

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


def course_id(key: str) -> uuid.UUID:
    return sid(f"course:{key}")


def purchase_id(key: str) -> uuid.UUID:
    return sid(f"purchase:{key}")


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
# Cases that need history use a customer of their own so they cannot pollute the
# others (the refund counters of BR-10 are per customer).
HISTORY_CUSTOMERS = (
    UserSpec("double-request", "Dana Pierce", "dana@example.com", RoleName.CUSTOMER),
    UserSpec("high-frequency", "Hugo Frost", "hugo@example.com", RoleName.CUSTOMER),
    UserSpec("abuse-limit", "Abby Lane", "abby@example.com", RoleName.CUSTOMER),
)
USERS = SUPERVISORS + CUSTOMERS + HISTORY_CUSTOMERS

# `ann` plays the "cust1" of the challenge: the owner of most cases. `bob` is the
# "other customer" whose purchase ann must not be able to see.
MAIN_CUSTOMER = "ann"
OTHER_CUSTOMER = "bob"
DECIDING_SUPERVISOR = "supervisor-1"

# Mirror the policy front-matter (`refund-eligibility`); only used to date the
# seed cases and to stamp the seeded refund requests.
POLICY_VERSION = "2026-10-01"
REFUND_WINDOW = timedelta(days=30)
# The "exact edge" case is 30 days minus this margin. The challenge says one
# second, which would expire before anyone can run a curl; the engine's own
# tests (spec 02) cover the exact second with an injected clock.
EDGE_MARGIN = timedelta(minutes=10)
CURRENCY = "USD"


@dataclass(frozen=True, slots=True)
class CourseSpec:
    key: str
    title: str
    instructor: str
    price: str


COURSES = (
    CourseSpec("python-bootcamp", "Complete Python Bootcamp", "Grace Ortega", "49.99"),
    CourseSpec(
        "fastapi-prod", "FastAPI: Production-Ready APIs", "Linus Okoye", "59.99"
    ),
    CourseSpec("react-19", "React 19 from Zero to Pro", "Maya Chen", "39.99"),
    CourseSpec("postgres-dev", "PostgreSQL for Developers", "Ravi Menon", "44.99"),
    CourseSpec("ml-az", "Machine Learning A-Z", "Elena Petrova", "79.99"),
    CourseSpec("docker-k8s", "Docker & Kubernetes Essentials", "Tomas Vidal", "54.99"),
)
COURSE_BY_KEY = {c.key: c for c in COURSES}


@dataclass(frozen=True, slots=True)
class CaseSpec:
    """One of the 13 edge cases: the purchase to create and what the engine must say.

    `eligible`, `reason_codes`, `refund_method` and `warnings` are the structured
    expectation that spec 02 reuses as a fixture for the policy engine tests.
    """

    key: str
    title: str
    expected: str
    eligible: bool
    age: timedelta
    progress: int
    course: str
    owner: str = MAIN_CUSTOMER
    asker: str = MAIN_CUSTOMER  # who checks eligibility (not the owner if foreign)
    reason_codes: tuple[str, ...] = ()
    refund_method: RefundMethod | None = None
    warnings: tuple[str, ...] = ()
    payment_method: PaymentMethod = PaymentMethod.CARD
    purchase_type: PurchaseType = PurchaseType.INDIVIDUAL
    status: PurchaseStatus = PurchaseStatus.ACTIVE
    certificate: bool = False
    pending_refund: bool = False  # an open PENDING_APPROVAL request on the purchase
    past_refunds: int = 0  # COMPLETED refunds on other purchases of the owner (12 mo)


CASES = (
    CaseSpec(
        "happy-path",
        "Camino feliz",
        "Elegible, ORIGINAL_PAYMENT",
        True,
        timedelta(days=5),
        10,
        "python-bootcamp",
        refund_method=RefundMethod.ORIGINAL_PAYMENT,
    ),
    CaseSpec(
        "exact-edge",
        "Borde exacto (dia 30 menos 10 min)",
        "Elegible, ORIGINAL_PAYMENT",
        True,
        REFUND_WINDOW - EDGE_MARGIN,
        5,
        "fastapi-prod",
        refund_method=RefundMethod.ORIGINAL_PAYMENT,
    ),
    CaseSpec(
        "outside-window",
        "Fuera de plazo (dia 31)",
        "OUTSIDE_REFUND_WINDOW",
        False,
        timedelta(days=31),
        5,
        "react-19",
        reason_codes=("OUTSIDE_REFUND_WINDOW",),
    ),
    CaseSpec(
        "high-consumption",
        "Consumo alto (62 %)",
        "CONSUMPTION_LIMIT_EXCEEDED",
        False,
        timedelta(days=10),
        62,
        "postgres-dev",
        reason_codes=("CONSUMPTION_LIMIT_EXCEEDED",),
    ),
    CaseSpec(
        "certificate-issued",
        "Certificado emitido",
        "CERTIFICATE_ISSUED",
        False,
        timedelta(days=8),
        25,  # under the consumption limit: only the certificate blocks it
        "ml-az",
        reason_codes=("CERTIFICATE_ISSUED",),
        certificate=True,
    ),
    CaseSpec(
        "gift",
        "Regalo (dia 3)",
        "Elegible, PLATFORM_CREDITS",
        True,
        timedelta(days=3),
        5,
        "docker-k8s",
        refund_method=RefundMethod.PLATFORM_CREDITS,
        purchase_type=PurchaseType.GIFT,
    ),
    CaseSpec(
        "subscription",
        "Suscripcion",
        "SUBSCRIPTION_NOT_ELIGIBLE",
        False,
        timedelta(days=4),
        5,
        "python-bootcamp",
        reason_codes=("SUBSCRIPTION_NOT_ELIGIBLE",),
        purchase_type=PurchaseType.SUBSCRIPTION,
    ),
    CaseSpec(
        "third-party",
        "Compra de tercero",
        "THIRD_PARTY_PURCHASE",
        False,
        timedelta(days=6),
        5,
        "fastapi-prod",
        reason_codes=("THIRD_PARTY_PURCHASE",),
        payment_method=PaymentMethod.PAYPAL,
        purchase_type=PurchaseType.THIRD_PARTY,
    ),
    CaseSpec(
        "already-refunded",
        "Ya reembolsada",
        "ALREADY_REFUNDED",
        False,
        timedelta(days=12),
        5,
        "react-19",
        reason_codes=("ALREADY_REFUNDED",),
        status=PurchaseStatus.REFUNDED,
    ),
    CaseSpec(
        "double-request",
        "Doble solicitud (una PENDING_APPROVAL)",
        "ACTIVE_REFUND_EXISTS",
        False,
        timedelta(days=7),
        10,
        "postgres-dev",
        owner="double-request",
        asker="double-request",
        reason_codes=("ACTIVE_REFUND_EXISTS",),
        pending_refund=True,
    ),
    CaseSpec(
        "high-frequency",
        "Frecuencia alta (3 reembolsos en 12 meses)",
        "Elegible, ORIGINAL_PAYMENT + HIGH_REFUND_FREQUENCY",
        True,
        timedelta(days=4),
        5,
        "ml-az",
        owner="high-frequency",
        asker="high-frequency",
        refund_method=RefundMethod.ORIGINAL_PAYMENT,
        warnings=("HIGH_REFUND_FREQUENCY",),
        past_refunds=3,
    ),
    CaseSpec(
        "abuse-limit",
        "Limite de abuso (5 reembolsos en 12 meses)",
        "REFUND_ABUSE_LIMIT",
        False,
        timedelta(days=4),
        5,
        "docker-k8s",
        owner="abuse-limit",
        asker="abuse-limit",
        reason_codes=("REFUND_ABUSE_LIMIT",),
        past_refunds=5,
    ),
    CaseSpec(
        "foreign-purchase",
        "Compra ajena (de bob, consultada por ann)",
        "PURCHASE_NOT_FOUND",
        False,
        timedelta(days=5),
        10,
        "python-bootcamp",
        owner=OTHER_CUSTOMER,
        asker=MAIN_CUSTOMER,
        reason_codes=("PURCHASE_NOT_FOUND",),
    ),
)


@dataclass(frozen=True, slots=True)
class TokenInfo:
    label: str
    role: RoleName
    token: str


@dataclass(frozen=True, slots=True)
class SeededCase:
    spec: CaseSpec
    purchase_id: uuid.UUID


@dataclass(slots=True)
class SeedReport:
    tokens: list[TokenInfo] = field(default_factory=list)
    users: dict[str, uuid.UUID] = field(default_factory=dict)
    cases: list[SeededCase] = field(default_factory=list)


async def _upsert[ModelT: Base](
    session: AsyncSession, model: type[ModelT], row_id: uuid.UUID, **values: object
) -> ModelT:
    """Create the row with this id, or bring an existing one back to `values`."""
    row = await session.get(model, row_id)
    if row is None:
        row = model(id=row_id, **values)
        session.add(row)
    else:
        for name, value in values.items():
            setattr(row, name, value)
    return row


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


async def _ensure_purchase(
    session: AsyncSession,
    key: str,
    *,
    owner: str,
    course: str,
    purchased_at: datetime,
    progress: int,
    status: PurchaseStatus = PurchaseStatus.ACTIVE,
    payment_method: PaymentMethod = PaymentMethod.CARD,
    purchase_type: PurchaseType = PurchaseType.INDIVIDUAL,
    certificate: bool = False,
    now: datetime,
) -> uuid.UUID:
    pid = purchase_id(key)
    await _upsert(
        session,
        Purchase,
        pid,
        customer_id=user_id(owner),
        course_id=course_id(course),
        amount=Decimal(COURSE_BY_KEY[course].price),
        currency=CURRENCY,
        payment_method=payment_method,
        purchase_type=purchase_type,
        purchased_at=purchased_at,
        status=status,
    )
    await session.flush()  # the progress row references the purchase
    await _upsert(
        session,
        CourseProgress,
        sid(f"progress:{key}"),
        purchase_id=pid,
        progress_pct=progress,
        lectures_completed=progress // 2,
        last_accessed_at=now - timedelta(days=1) if progress else None,
        certificate_issued_at=purchased_at + timedelta(days=1) if certificate else None,
    )
    return pid


async def _ensure_refund(
    session: AsyncSession,
    key: str,
    *,
    purchase: uuid.UUID,
    owner: str,
    course: str,
    status: RefundStatus,
    created_at: datetime,
) -> None:
    """A refund request of `owner` plus the events its status implies.

    Events are append-only, so only the missing ones are added; the request row
    itself is brought back to the seeded state on every run.
    """
    refund_id = sid(f"refund:{key}")
    decided = status is not RefundStatus.PENDING_APPROVAL
    decided_at = created_at + timedelta(days=1)
    await _upsert(
        session,
        RefundRequest,
        refund_id,
        customer_id=user_id(owner),
        purchase_id=purchase,
        status=status,
        reason_category=ReasonCategory.NOT_AS_DESCRIBED,
        reason_text="The course content does not match what the description promised.",
        refund_method=RefundMethod.ORIGINAL_PAYMENT,
        amount=Decimal(COURSE_BY_KEY[course].price),
        currency=CURRENCY,
        channel=Channel.MANUAL_FORM,
        eligibility_snapshot={"seed": True},
        policy_version=POLICY_VERSION,
        confirmed_at=created_at,
        confirmation_hash=f"seed-{key}",
        decided_by=user_id(DECIDING_SUPERVISOR) if decided else None,
        decided_at=decided_at if decided else None,
        decision_note="Approved (seed data)." if decided else None,
        idempotency_key=f"seed-{key}",
        version=2 if decided else 1,
        created_at=created_at,
        updated_at=decided_at if decided else created_at,
    )
    await session.flush()

    wanted: list[tuple[str, ActorType, str | None, datetime]] = [
        ("REFUND_CREATED", ActorType.CUSTOMER, owner, created_at)
    ]
    if decided:
        wanted += [
            ("REFUND_APPROVED", ActorType.SUPERVISOR, DECIDING_SUPERVISOR, decided_at),
            ("REFUND_COMPLETED", ActorType.SYSTEM, None, decided_at),
        ]
    events = RefundEventService(session)
    existing = {e.type for e in await events.repo.list_for_refund(refund_id)}
    for event_type, actor_type, actor, at in wanted:
        if event_type not in existing:
            await events.append(
                refund_id,
                event_type,
                EventActor(actor_type, user_id(actor) if actor else None),
                {"seed": True},
                created_at=at,
            )


async def _seed_courses(session: AsyncSession) -> None:
    for spec in COURSES:
        await _upsert(
            session,
            Course,
            course_id(spec.key),
            title=spec.title,
            instructor_name=spec.instructor,
            price=Decimal(spec.price),
            currency=CURRENCY,
        )
    await session.flush()


async def _seed_case(session: AsyncSession, case: CaseSpec, now: datetime) -> uuid.UUID:
    pid = await _ensure_purchase(
        session,
        case.key,
        owner=case.owner,
        course=case.course,
        purchased_at=now - case.age,
        progress=case.progress,
        status=case.status,
        payment_method=case.payment_method,
        purchase_type=case.purchase_type,
        certificate=case.certificate,
        now=now,
    )
    if case.status is PurchaseStatus.REFUNDED:
        await _ensure_refund(
            session,
            case.key,
            purchase=pid,
            owner=case.owner,
            course=case.course,
            status=RefundStatus.COMPLETED,
            created_at=now - case.age + timedelta(days=1),
        )
    if case.pending_refund:
        await _ensure_refund(
            session,
            case.key,
            purchase=pid,
            owner=case.owner,
            course=case.course,
            status=RefundStatus.PENDING_APPROVAL,
            created_at=now - timedelta(days=1),
        )
    # Refund history that counts for BR-10: COMPLETED refunds on older purchases,
    # all well inside the last 12 months.
    for i in range(case.past_refunds):
        course = COURSES[i % len(COURSES)].key
        past_key = f"{case.key}:past:{i}"
        bought = now - timedelta(days=60 + 30 * i)
        past = await _ensure_purchase(
            session,
            past_key,
            owner=case.owner,
            course=course,
            purchased_at=bought,
            progress=5,
            status=PurchaseStatus.REFUNDED,
            now=now,
        )
        await _ensure_refund(
            session,
            past_key,
            purchase=past,
            owner=case.owner,
            course=course,
            status=RefundStatus.COMPLETED,
            created_at=bought + timedelta(days=1),
        )
    await session.flush()
    return pid


async def reset(session: AsyncSession) -> None:
    """Delete only the rows that belong to the seed users (and the seed courses)."""
    users = [user_id(spec.key) for spec in USERS]
    refunds = select(RefundRequest.id).where(RefundRequest.customer_id.in_(users))
    purchases = select(Purchase.id).where(Purchase.customer_id.in_(users))

    # `refund_events` rejects DELETE by design. Dev data is the one place where we
    # lift that, inside this transaction only (DDL is transactional in Postgres,
    # so a failure rolls the trigger back to enabled).
    table = RefundEvent.__tablename__
    await session.execute(
        text(f"ALTER TABLE {table} DISABLE TRIGGER {APPEND_ONLY_TRIGGER}")
    )
    await session.execute(delete(RefundEvent).where(RefundEvent.refund_id.in_(refunds)))
    await session.execute(
        text(f"ALTER TABLE {table} ENABLE TRIGGER {APPEND_ONLY_TRIGGER}")
    )

    await session.execute(
        delete(RefundInternalNote).where(RefundInternalNote.refund_id.in_(refunds))
    )
    await session.execute(
        delete(RefundPayout).where(RefundPayout.refund_id.in_(refunds))
    )
    await session.execute(
        delete(CreditLedgerEntry).where(CreditLedgerEntry.customer_id.in_(users))
    )
    await session.execute(delete(RefundRequest).where(RefundRequest.id.in_(refunds)))
    await session.execute(
        delete(EligibilityCheck).where(EligibilityCheck.customer_id.in_(users))
    )
    await session.execute(
        delete(CourseProgress).where(CourseProgress.purchase_id.in_(purchases))
    )
    await session.execute(delete(Purchase).where(Purchase.id.in_(purchases)))
    await session.execute(delete(ChatThread).where(ChatThread.customer_id.in_(users)))
    await session.execute(
        delete(Course).where(Course.id.in_([course_id(c.key) for c in COURSES]))
    )
    await session.execute(delete(User).where(User.id.in_(users)))  # cascades user_roles
    await session.flush()


async def seed(
    session: AsyncSession, *, ttl: timedelta, now: datetime | None = None
) -> SeedReport:
    """Create (or complete) the seed data; safe to run repeatedly.

    `now` is injectable for tests; every purchase date is computed from it.
    """
    now = now or datetime.now(UTC)
    report = SeedReport()
    roles = await ensure_rbac(session)
    password_hash = hash_password(SEED_PASSWORD)  # one hash shared by every seed user
    for spec in USERS:
        report.users[spec.key] = await _ensure_user(session, spec, roles, password_hash)
    await session.flush()

    await _seed_courses(session)
    for case in CASES:
        report.cases.append(SeededCase(case, await _seed_case(session, case, now)))

    for spec in USERS:
        report.tokens.append(
            TokenInfo(
                spec.key,
                spec.role,
                create_access_token(user_id=report.users[spec.key], expires_in=ttl),
            )
        )
    return report


def render_cases(report: SeedReport) -> list[str]:
    """The `case -> purchase_id -> expected result` table (spec 02 fixture)."""
    out = [
        "",
        f"Cases ({len(report.cases)}): purchase_id -> expected eligibility "
        "(checked by `as`)",
        f"  {'#':>2}  {'case':<44} {'as':<15} {'purchase_id':<36}  expected",
    ]
    for n, seeded in enumerate(report.cases, start=1):
        spec = seeded.spec
        out.append(
            f"  {n:>2}  {spec.title:<44} {spec.asker:<15} "
            f"{seeded.purchase_id}  {spec.expected}"
        )
    return out


def render(report: SeedReport, *, show_tokens: bool, base_url: str) -> str:
    out: list[str] = ["", "Seed data ready", "", f"Users (password: {SEED_PASSWORD})"]
    for spec in USERS:
        out.append(
            f"  {spec.key:<14} {spec.role.value:<11} {spec.email:<24} "
            f"{report.users[spec.key]}"
        )
    out += render_cases(report)

    sup1 = "$SUPERVISOR_1" if show_tokens else "<supervisor-1 token>"
    ann = "$ANN" if show_tokens else "<ann token>"
    foreign = next(c for c in report.cases if c.spec.key == "foreign-purchase")
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
    out.append(f'  curl -H "Authorization: Bearer {ann}" "{base_url}/api/v1/me"')
    out.append(f'  curl -H "Authorization: Bearer {ann}" "{base_url}/api/v1/purchases"')
    out.append(
        f'  curl -H "Authorization: Bearer {ann}" '
        f'"{base_url}/api/v1/purchases/{foreign.purchase_id}"   # bob\'s -> 404'
    )
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
                # The engine needs the active policy: load it first (idempotent).
                await load_policies(session, default_policies_dir())
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
