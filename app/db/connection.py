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
    user_name VARCHAR(255),
    query_type VARCHAR(50),
    status VARCHAR(50) NOT NULL DEFAULT 'received',
    vapi_call_id VARCHAR(255),
    transcript TEXT,
    tool_calls_made JSONB,
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
    transcript_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS logistics_orders (
    id SERIAL PRIMARY KEY,
    ticket_id VARCHAR(255) NOT NULL REFERENCES tickets(ticket_id) ON DELETE CASCADE,
    store_call_id INTEGER REFERENCES store_calls(id),
    client_order_id VARCHAR(255) NOT NULL UNIQUE,
    prorouting_order_id VARCHAR(255),
    quote_id VARCHAR(255),
    selected_lsp_id VARCHAR(500),
    selected_lsp_name VARCHAR(500),
    quoted_price DECIMAL(12,2),
    pickup_lat DECIMAL(10,8),
    pickup_lng DECIMAL(11,8),
    pickup_address TEXT,
    pickup_pincode VARCHAR(10),
    pickup_phone VARCHAR(50),
    drop_lat DECIMAL(10,8),
    drop_lng DECIMAL(11,8),
    drop_address TEXT,
    drop_pincode VARCHAR(10),
    drop_phone VARCHAR(50),
    customer_name VARCHAR(255),
    order_state VARCHAR(100) DEFAULT 'pending',
    rider_name VARCHAR(255),
    rider_phone VARCHAR(50),
    tracking_url TEXT,
    status_callbacks JSONB DEFAULT '[]'::jsonb,
    order_amount DECIMAL(12,2),
    order_weight DECIMAL(8,2) DEFAULT 1.0,
    error_message TEXT,
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
CREATE INDEX IF NOT EXISTS idx_logistics_orders_ticket ON logistics_orders(ticket_id);
CREATE INDEX IF NOT EXISTS idx_logistics_orders_prorouting ON logistics_orders(prorouting_order_id);
CREATE INDEX IF NOT EXISTS idx_logistics_orders_client ON logistics_orders(client_order_id);
"""

_MIGRATION_SQL = """
DO $$ BEGIN
    ALTER TABLE tickets ADD COLUMN IF NOT EXISTS vapi_call_id VARCHAR(255);
    ALTER TABLE tickets ADD COLUMN IF NOT EXISTS transcript TEXT;
    ALTER TABLE tickets ADD COLUMN IF NOT EXISTS tool_calls_made JSONB;
    ALTER TABLE tickets ADD COLUMN IF NOT EXISTS user_name VARCHAR(255);
    ALTER TABLE store_calls ADD COLUMN IF NOT EXISTS transcript_json JSONB;
EXCEPTION WHEN others THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_tickets_vapi_call ON tickets(vapi_call_id);
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
    """Create tables if they do not exist, then run migrations."""
    with get_connection(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA_SQL)
            cur.execute(_MIGRATION_SQL)
    logger.info("Database schema initialized")
