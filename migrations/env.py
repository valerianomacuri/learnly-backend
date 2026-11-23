import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401  (registers all tables on Base.metadata)
from app.core.config import get_settings
from app.models.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Tables owned by LangGraph's AsyncPostgresSaver, created by its own setup() at app
# startup (spec 09); they are not models, so autogenerate/check must not treat them
# as drift.
CHECKPOINTER_TABLES = frozenset(
    {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
)


def include_object(object, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]  # noqa: A002
    if type_ == "table" and reflected and name in CHECKPOINTER_TABLES:
        return False
    # Indexes of those tables are reflected too.
    table = getattr(object, "table", None)
    if type_ == "index" and table is not None and table.name in CHECKPOINTER_TABLES:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    # psycopg async can't use Windows' default ProactorEventLoop.
    asyncio.run(run_migrations_online(), loop_factory=asyncio.SelectorEventLoop)
