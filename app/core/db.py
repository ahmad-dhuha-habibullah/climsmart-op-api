"""SQLAlchemy 2 engine/session. Portable types only (works on SQLite and PostgreSQL)."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str):
        self.url = url
        kwargs: dict = {"future": True, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            path = url.split("///", 1)[-1]
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        elif url.startswith("postgresql"):
            from sqlalchemy.pool import NullPool
            # serverless + Supabase/PgBouncer transaction pooler: no client pool, no server-side prepared statements
            kwargs["poolclass"] = NullPool
            if "+psycopg" in url:
                kwargs["connect_args"] = {"prepare_threshold": None}
        self.engine: Engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _pragmas(conn, _):  # FK enforcement + better concurrency for API + worker on one file
                cur = conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA journal_mode=WAL")
                cur.close()
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self.SessionLocal()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()


def run_migrations(url: str) -> None:
    """alembic upgrade head, in-process (used at startup when DB_AUTO_MIGRATE is on)."""
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.attributes["url"] = url
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
