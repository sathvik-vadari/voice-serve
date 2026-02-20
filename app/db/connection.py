"""PostgreSQL connection and schema init for wake-up calls."""
import logging
from contextlib import contextmanager
from typing import Generator, Optional

import psycopg2
from psycopg2.extensions import connection as PgConnection

from app.helpers.config import Config

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS wakeup_users (
    id SERIAL PRIMARY KEY,
    user_identifier VARCHAR(255) NOT NULL UNIQUE,
    do_not_call BOOLEAN NOT NULL DEFAULT FALSE,
    daily_wakeup_time TIME,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scheduled_calls (
    id SERIAL PRIMARY KEY,
    user_identifier VARCHAR(255) NOT NULL REFERENCES wakeup_users(user_identifier) ON DELETE CASCADE,
    scheduled_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scheduled_calls_at ON scheduled_calls(scheduled_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_wakeup_users_identifier ON wakeup_users(user_identifier);
"""


@contextmanager
def get_connection(db_url: Optional[str] = None) -> Generator[PgConnection, None, None]:
    """Get a database connection. Uses DATABASE_URL from config if db_url not provided."""
    url = db_url or Config.DATABASE_URL
    if not url:
        raise ValueError("DATABASE_URL is not set")
    conn = psycopg2.connect(url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_url: Optional[str] = None) -> None:
    """Create tables if they do not exist."""
    with get_connection(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA_SQL)
    logger.info("Database schema initialized")
