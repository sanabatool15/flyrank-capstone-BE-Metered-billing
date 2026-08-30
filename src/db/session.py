"""Database engine, session factory, and declarative Base.

Repositories are the only layer allowed to import from this module directly
(per the routers -> services -> repositories layering rule in CLAUDE.md).
"""
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker

from src.config import get_settings

DATABASE_URL = get_settings().DATABASE_URL


def _supabase_connect_args(database_url: str) -> dict:
    """Return connection options required by Supabase Postgres.

    Supabase requires TLS for database connections.  Keeping this here means
    both the direct endpoint and the Supavisor session-pooler URL work without
    requiring users to remember an extra query parameter.
    """
    url = make_url(database_url)
    if url.get_backend_name() == "postgresql":
        return {"sslmode": "require"}
    return {}

engine = create_engine(
    DATABASE_URL,
    connect_args=_supabase_connect_args(DATABASE_URL),
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
