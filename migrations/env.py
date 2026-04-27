import os
from logging.config import fileConfig
from sqlalchemy import create_engine, pool
from alembic import context

# Import models so metadata is populated
from app.database import Base
import app.models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url(url: str) -> str:
    """
    Alembic migrations run synchronously with psycopg2.
    Convert any asyncpg/async URL back to plain postgresql://.
    Railway provides postgresql:// or postgres:// — both work with psycopg2.
    """
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    url = url.replace("postgres://", "postgresql://")
    return url


def _get_url() -> str:
    # Prefer DATABASE_URL env var (set by Railway) over alembic.ini
    return _sync_url(
        os.environ.get("DATABASE_URL")
        or config.get_main_option("sqlalchemy.url")
        or "postgresql://tennisbot:tennisbot@localhost:5432/tennisbot"
    )


def run_migrations_offline() -> None:
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _get_url()
    connectable = create_engine(url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
