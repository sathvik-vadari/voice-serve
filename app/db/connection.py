"""PostgreSQL connection and schema init."""
import logging
from contextlib import contextmanager
from typing import Generator, Optional

import psycopg2
from psycopg2.extensions import connection as PgConnection

from app.helpers.config import Config

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
-- ==================== Wake-up call tables (existing) ====================

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

CREATE INDEX IF NOT EXISTS idx_scheduled_calls_at
    ON scheduled_calls(scheduled_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_wakeup_users_identifier
    ON wakeup_users(user_identifier);

-- ==================== Commerce / ticket tables ====================

CREATE TABLE IF NOT EXISTS tickets (
    id SERIAL PRIMARY KEY,
    ticket_id VARCHAR(255) NOT NULL UNIQUE,
    query TEXT NOT NULL,
    location TEXT NOT NULL,
    user_phone VARCHAR(50),
    query_type VARCHAR(50),
    status VARCHAR(50) NOT NULL DEFAULT 'received',
    final_result JSONB,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ticket_products (
    id SERIAL PRIMARY KEY,
    ticket_id VARCHAR(255) NOT NULL REFERENCES tickets(ticket_id) ON DELETE CASCADE,
    product_name VARCHAR(500) NOT NULL,
    product_category VARCHAR(255),
    product_specs JSONB,
    avg_price_online DECIMAL(12,2),
    alternatives JSONB,
    store_search_query VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ticket_stores (
    id SERIAL PRIMARY KEY,
    ticket_id VARCHAR(255) NOT NULL REFERENCES tickets(ticket_id) ON DELETE CASCADE,
    store_name VARCHAR(500) NOT NULL,
    address TEXT,
    phone_number VARCHAR(50),
    rating DECIMAL(3,2),
    total_ratings INTEGER,
    place_id VARCHAR(255),
    latitude DECIMAL(10,8),
    longitude DECIMAL(11,8),
    call_priority INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS store_calls (
    id SERIAL PRIMARY KEY,
    ticket_id VARCHAR(255) NOT NULL REFERENCES tickets(ticket_id) ON DELETE CASCADE,
    store_id INTEGER NOT NULL REFERENCES ticket_stores(id) ON DELETE CASCADE,
    vapi_call_id VARCHAR(255),
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    transcript TEXT,
    tool_calls_raw JSONB,
    call_analysis JSONB,
    product_available BOOLEAN,
    matched_product VARCHAR(500),
    price DECIMAL(12,2),
    delivery_available BOOLEAN,
    delivery_eta VARCHAR(255),
    delivery_mode VARCHAR(255),
    delivery_charge DECIMAL(12,2),
    product_match_type VARCHAR(50),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS llm_logs (
    id SERIAL PRIMARY KEY,
    ticket_id VARCHAR(255) REFERENCES tickets(ticket_id) ON DELETE SET NULL,
    step VARCHAR(100) NOT NULL,
    model VARCHAR(100),
    prompt_template VARCHAR(255),
    input_data JSONB,
    output_data JSONB,
    raw_response TEXT,
    tokens_input INTEGER,
    tokens_output INTEGER,
    latency_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tool_call_logs (
    id SERIAL PRIMARY KEY,
    ticket_id VARCHAR(255) REFERENCES tickets(ticket_id) ON DELETE SET NULL,
    store_call_id INTEGER REFERENCES store_calls(id) ON DELETE SET NULL,
    tool_name VARCHAR(255) NOT NULL,
    input_params JSONB,
    output_result JSONB,
    status VARCHAR(50) NOT NULL DEFAULT 'success',
    error_message TEXT,
    latency_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tickets_ticket_id ON tickets(ticket_id);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_store_calls_vapi_id ON store_calls(vapi_call_id);
CREATE INDEX IF NOT EXISTS idx_store_calls_ticket ON store_calls(ticket_id);
CREATE INDEX IF NOT EXISTS idx_llm_logs_ticket ON llm_logs(ticket_id);
CREATE INDEX IF NOT EXISTS idx_tool_logs_ticket ON tool_call_logs(ticket_id);
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
