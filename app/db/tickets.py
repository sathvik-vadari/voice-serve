"""DB operations for the commerce/ticket pipeline."""
import json
import logging
from typing import Any, Optional

from app.db.connection import get_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

def get_next_ticket_id() -> str:
    """Generate the next ticket ID by finding the highest existing TKT-NNN and incrementing."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT ticket_id FROM tickets
                   WHERE ticket_id LIKE 'TKT-%%'
                   ORDER BY created_at DESC, id DESC LIMIT 1""",
            )
            row = cur.fetchone()
    if not row:
        return "TKT-001"
    last_id = row[0]
    try:
        num = int(last_id.split("-", 1)[1])
        return f"TKT-{num + 1:03d}"
    except (ValueError, IndexError):
        return "TKT-001"


def ticket_exists_and_active(ticket_id: str) -> bool:
    """Check if a ticket already exists and is still being processed."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM tickets WHERE ticket_id = %s",
                (ticket_id,),
            )
            row = cur.fetchone()
    if not row:
        return False
    active_statuses = (
        "received", "classifying", "analyzing", "researching",
        "finding_stores", "calling_stores", "wakeup_calling", "wakeup_in_progress",
    )
    return row[0] in active_statuses


def create_ticket(ticket_id: str, query: str, location: str, user_phone: Optional[str] = None) -> dict[str, Any]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tickets (ticket_id, query, location, user_phone, status)
                   VALUES (%s, %s, %s, %s, 'received')
                   RETURNING id, ticket_id, status, created_at""",
                (ticket_id, query, location, user_phone),
            )
            row = cur.fetchone()
    return {"id": row[0], "ticket_id": row[1], "status": row[2], "created_at": row[3].isoformat()}


def update_ticket_status(ticket_id: str, status: str, *, error_message: Optional[str] = None) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE tickets SET status = %s, error_message = %s, updated_at = NOW()
                   WHERE ticket_id = %s""",
                (status, error_message, ticket_id),
            )


def update_ticket_query_type(ticket_id: str, query_type: str) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tickets SET query_type = %s, updated_at = NOW() WHERE ticket_id = %s",
                (query_type, ticket_id),
            )


def set_ticket_final_result(ticket_id: str, result: dict) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE tickets SET final_result = %s, status = 'completed', updated_at = NOW()
                   WHERE ticket_id = %s""",
                (json.dumps(result, default=str), ticket_id),
            )


def get_ticket(ticket_id: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, ticket_id, query, location, user_phone, query_type,
                          status, vapi_call_id, transcript, tool_calls_made,
                          final_result, error_message, created_at, updated_at
                   FROM tickets WHERE ticket_id = %s""",
                (ticket_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "ticket_id": row[1], "query": row[2], "location": row[3],
        "user_phone": row[4], "query_type": row[5], "status": row[6],
        "vapi_call_id": row[7], "transcript": row[8], "tool_calls_made": row[9],
        "final_result": row[10], "error_message": row[11],
        "created_at": row[12].isoformat() if row[12] else None,
        "updated_at": row[13].isoformat() if row[13] else None,
    }


def set_ticket_vapi_call_id(ticket_id: str, vapi_call_id: str) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tickets SET vapi_call_id = %s, updated_at = NOW() WHERE ticket_id = %s",
                (vapi_call_id, ticket_id),
            )


def get_ticket_by_vapi_call_id(vapi_call_id: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ticket_id FROM tickets WHERE vapi_call_id = %s",
                (vapi_call_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return get_ticket(row[0])


def append_ticket_tool_call(ticket_id: str, tool_call: dict) -> None:
    """Append a tool call record to the ticket's tool_calls_made JSONB array."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE tickets
                   SET tool_calls_made = COALESCE(tool_calls_made, '[]'::jsonb) || %s::jsonb,
                       updated_at = NOW()
                   WHERE ticket_id = %s""",
                (json.dumps([tool_call], default=str), ticket_id),
            )


def save_ticket_transcript(ticket_id: str, transcript: str, ended_reason: str) -> None:
    """Save the call transcript and update the final result with full call details."""
    ticket = get_ticket(ticket_id)
    if not ticket:
        return
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tickets SET transcript = %s, updated_at = NOW() WHERE ticket_id = %s",
                (transcript, ticket_id),
            )

    existing_result = ticket.get("final_result") or {}
    existing_result["transcript"] = transcript
    existing_result["ended_reason"] = ended_reason
    existing_result["tool_calls_made"] = ticket.get("tool_calls_made") or []
    set_ticket_final_result(ticket_id, existing_result)


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

def save_product(ticket_id: str, product: dict[str, Any]) -> int:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ticket_products
                       (ticket_id, product_name, product_category, product_specs,
                        avg_price_online, alternatives, store_search_query)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    ticket_id,
                    product["product_name"],
                    product.get("product_category"),
                    json.dumps(product.get("specs") or {}),
                    product.get("avg_price_online"),
                    json.dumps(product.get("alternatives") or []),
                    product.get("store_search_query"),
                ),
            )
            return cur.fetchone()[0]


def get_product(ticket_id: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, product_name, product_category, product_specs,
                          avg_price_online, alternatives, store_search_query
                   FROM ticket_products WHERE ticket_id = %s ORDER BY id DESC LIMIT 1""",
                (ticket_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "product_name": row[1], "product_category": row[2],
        "specs": row[3], "avg_price_online": float(row[4]) if row[4] else None,
        "alternatives": row[5], "store_search_query": row[6],
    }


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------

def save_stores(ticket_id: str, stores: list[dict[str, Any]]) -> list[int]:
    ids = []
    with get_connection() as conn:
        with conn.cursor() as cur:
            for idx, s in enumerate(stores):
                place_id = s.get("place_id")
                if place_id:
                    cur.execute(
                        "SELECT id FROM ticket_stores WHERE ticket_id = %s AND place_id = %s",
                        (ticket_id, place_id),
                    )
                    existing = cur.fetchone()
                    if existing:
                        ids.append(existing[0])
                        continue

                cur.execute(
                    """INSERT INTO ticket_stores
                           (ticket_id, store_name, address, phone_number,
                            rating, total_ratings, place_id, latitude, longitude, call_priority)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (
                        ticket_id, s["name"], s.get("address"), s.get("phone_number"),
                        s.get("rating"), s.get("total_ratings"), place_id,
                        s.get("latitude"), s.get("longitude"), idx + 1,
                    ),
                )
                ids.append(cur.fetchone()[0])
    return ids


def update_store_priorities(ticket_id: str, ordered_place_ids: list[str]) -> None:
    """Re-order stores by updating call_priority based on the given place_id order."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            for priority, place_id in enumerate(ordered_place_ids, 1):
                cur.execute(
                    """UPDATE ticket_stores SET call_priority = %s
                       WHERE ticket_id = %s AND place_id = %s""",
                    (priority, ticket_id, place_id),
                )


def get_stores(ticket_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT ON (place_id)
                          id, store_name, address, phone_number, rating, total_ratings,
                          place_id, call_priority
                   FROM ticket_stores WHERE ticket_id = %s
                   ORDER BY place_id, call_priority, id""",
                (ticket_id,),
            )
            rows = cur.fetchall()
    stores = [
        {"id": r[0], "store_name": r[1], "address": r[2], "phone_number": r[3],
         "rating": float(r[4]) if r[4] else None, "total_ratings": r[5],
         "place_id": r[6], "call_priority": r[7]}
        for r in rows
    ]
    stores.sort(key=lambda s: s["call_priority"] or 999)
    return stores


# ---------------------------------------------------------------------------
# Store calls
# ---------------------------------------------------------------------------

def create_store_call(ticket_id: str, store_id: int) -> int:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO store_calls (ticket_id, store_id, status)
                   VALUES (%s, %s, 'pending') RETURNING id""",
                (ticket_id, store_id),
            )
            return cur.fetchone()[0]


def update_store_call_vapi_id(call_id: int, vapi_call_id: str) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE store_calls SET vapi_call_id = %s, status = 'calling', updated_at = NOW() WHERE id = %s",
                (vapi_call_id, call_id),
            )


def update_store_call_status(call_id: int, status: str) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE store_calls SET status = %s, updated_at = NOW() WHERE id = %s",
                (status, call_id),
            )


def get_store_call_by_vapi_id(vapi_call_id: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT sc.id, sc.ticket_id, sc.store_id, sc.status,
                          ts.store_name, ts.phone_number
                   FROM store_calls sc
                   JOIN ticket_stores ts ON ts.id = sc.store_id
                   WHERE sc.vapi_call_id = %s""",
                (vapi_call_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "ticket_id": row[1], "store_id": row[2], "status": row[3],
        "store_name": row[4], "phone_number": row[5],
    }


def save_store_call_transcript(vapi_call_id: str, transcript: str) -> Optional[int]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE store_calls SET transcript = %s, status = 'transcript_received', updated_at = NOW()
                   WHERE vapi_call_id = %s RETURNING id""",
                (transcript, vapi_call_id),
            )
            row = cur.fetchone()
    return row[0] if row else None


def save_store_call_analysis(call_id: int, analysis: dict[str, Any]) -> None:
    notes_parts = []
    if analysis.get("call_summary"):
        notes_parts.append(analysis["call_summary"])
    if analysis.get("notes"):
        notes_parts.append(analysis["notes"])
    combined_notes = " | ".join(notes_parts) if notes_parts else None

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE store_calls SET
                       call_analysis = %s,
                       product_available = %s,
                       matched_product = %s,
                       price = %s,
                       delivery_available = %s,
                       delivery_eta = %s,
                       delivery_mode = %s,
                       delivery_charge = %s,
                       product_match_type = %s,
                       notes = %s,
                       status = 'analyzed',
                       updated_at = NOW()
                   WHERE id = %s""",
                (
                    json.dumps(analysis, default=str),
                    analysis.get("product_available"),
                    analysis.get("matched_product"),
                    analysis.get("price"),
                    analysis.get("delivery_available"),
                    analysis.get("delivery_eta"),
                    analysis.get("delivery_mode"),
                    analysis.get("delivery_charge"),
                    analysis.get("product_match_type"),
                    combined_notes,
                    call_id,
                ),
            )


def save_store_call_tool_calls(vapi_call_id: str, tool_calls: list[dict]) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE store_calls SET tool_calls_raw = %s, updated_at = NOW() WHERE vapi_call_id = %s",
                (json.dumps(tool_calls, default=str), vapi_call_id),
            )


def get_store_calls_for_ticket(ticket_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT sc.id, sc.store_id, sc.vapi_call_id, sc.status,
                          sc.product_available, sc.matched_product, sc.price,
                          sc.delivery_available, sc.delivery_eta, sc.delivery_mode,
                          sc.delivery_charge, sc.product_match_type, sc.notes,
                          sc.call_analysis, ts.store_name, ts.phone_number, ts.rating
                   FROM store_calls sc
                   JOIN ticket_stores ts ON ts.id = sc.store_id
                   WHERE sc.ticket_id = %s ORDER BY ts.call_priority""",
                (ticket_id,),
            )
            rows = cur.fetchall()
    return [
        {
            "id": r[0], "store_id": r[1], "vapi_call_id": r[2], "status": r[3],
            "product_available": r[4], "matched_product": r[5],
            "price": float(r[6]) if r[6] else None,
            "delivery_available": r[7], "delivery_eta": r[8],
            "delivery_mode": r[9],
            "delivery_charge": float(r[10]) if r[10] else None,
            "product_match_type": r[11], "notes": r[12],
            "call_analysis": r[13], "store_name": r[14],
            "phone_number": r[15], "rating": float(r[16]) if r[16] else None,
        }
        for r in rows
    ]


def count_pending_calls(ticket_id: str) -> int:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) FROM store_calls
                   WHERE ticket_id = %s AND status NOT IN ('analyzed', 'failed')""",
                (ticket_id,),
            )
            return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# LLM logs
# ---------------------------------------------------------------------------

def log_llm_call(
    ticket_id: str, step: str, model: str, prompt_template: str,
    input_data: Any, output_data: Any, raw_response: str,
    tokens_input: int = 0, tokens_output: int = 0, latency_ms: int = 0,
) -> int:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO llm_logs
                       (ticket_id, step, model, prompt_template, input_data, output_data,
                        raw_response, tokens_input, tokens_output, latency_ms)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (
                    ticket_id, step, model, prompt_template,
                    json.dumps(input_data, default=str),
                    json.dumps(output_data, default=str),
                    raw_response, tokens_input, tokens_output, latency_ms,
                ),
            )
            return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Tool call logs
# ---------------------------------------------------------------------------

def log_tool_call(
    ticket_id: str, tool_name: str, input_params: Any, output_result: Any,
    status: str = "success", error_message: Optional[str] = None,
    store_call_id: Optional[int] = None, latency_ms: int = 0,
) -> int:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tool_call_logs
                       (ticket_id, store_call_id, tool_name, input_params, output_result,
                        status, error_message, latency_ms)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (
                    ticket_id, store_call_id, tool_name,
                    json.dumps(input_params, default=str),
                    json.dumps(output_result, default=str),
                    status, error_message, latency_ms,
                ),
            )
            return cur.fetchone()[0]
