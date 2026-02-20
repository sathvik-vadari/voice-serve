"""Wake-up call user preferences and scheduled calls (DB operations)."""
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.db.connection import get_connection


def get_or_create_user(user_identifier: str) -> dict[str, Any]:
    """Get user row by identifier; create if not exists. Returns user dict."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO wakeup_users (user_identifier)
                VALUES (%s)
                ON CONFLICT (user_identifier) DO UPDATE SET updated_at = NOW()
                RETURNING id, user_identifier, do_not_call, daily_wakeup_time
                """,
                (user_identifier,),
            )
            row = cur.fetchone()
    return {
        "id": row[0],
        "user_identifier": row[1],
        "do_not_call": row[2],
        "daily_wakeup_time": str(row[3]) if row[3] else None,
    }


def schedule_wakeup_in_minutes(user_identifier: str, minutes: int) -> dict[str, Any]:
    """Schedule a one-off wake-up call in `minutes` from now. Returns scheduled time."""
    get_or_create_user(user_identifier)
    scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scheduled_calls (user_identifier, scheduled_at, status)
                VALUES (%s, %s, 'pending')
                RETURNING id, scheduled_at
                """,
                (user_identifier, scheduled_at),
            )
            row = cur.fetchone()
    return {
        "success": True,
        "scheduled_at": row[1].isoformat(),
        "in_minutes": minutes,
        "message": f"Wake-up call scheduled in {minutes} minutes.",
    }


def set_never_call_again(user_identifier: str) -> dict[str, Any]:
    """Set do_not_call for user and cancel any pending scheduled calls. Future wake-up calls (including daily) should be skipped."""
    get_or_create_user(user_identifier)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE wakeup_users
                SET do_not_call = TRUE, updated_at = NOW()
                WHERE user_identifier = %s
                """,
                (user_identifier,),
            )
            cur.execute(
                """
                UPDATE scheduled_calls
                SET status = 'cancelled'
                WHERE user_identifier = %s AND status = 'pending'
                """,
                (user_identifier,),
            )
    return {
        "success": True,
        "message": "You will not receive any more wake-up calls.",
    }


def set_daily_wakeup_time(user_identifier: str, time_str: str) -> dict[str, Any]:
    """Set daily wake-up time. time_str should be HH:MM or HH:MM AM/PM (24h stored)."""
    get_or_create_user(user_identifier)
    # Parse flexible time: "7:30", "07:30", "7:30 AM", "19:30"
    time_str = time_str.strip().upper()
    try:
        if "AM" in time_str or "PM" in time_str:
            t = datetime.strptime(time_str.replace("AM", "").replace("PM", "").strip(), "%I:%M")
            if "PM" in time_str and t.hour != 12:
                t = t.replace(hour=t.hour + 12)
            elif "AM" in time_str and t.hour == 12:
                t = t.replace(hour=0)
            wakeup_time = t.time()
        else:
            wakeup_time = datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        return {"success": False, "error": f"Could not parse time: {time_str}. Use HH:MM or HH:MM AM/PM."}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE wakeup_users
                SET daily_wakeup_time = %s, updated_at = NOW()
                WHERE user_identifier = %s
                """,
                (wakeup_time, user_identifier),
            )
    return {
        "success": True,
        "daily_wakeup_time": wakeup_time.strftime("%H:%M"),
        "message": f"Daily wake-up call set for {wakeup_time.strftime('%I:%M %p')}.",
    }


def get_user_prefs(user_identifier: str) -> dict[str, Any]:
    """Get user preferences (do_not_call, daily_wakeup_time)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT do_not_call, daily_wakeup_time
                FROM wakeup_users
                WHERE user_identifier = %s
                """,
                (user_identifier,),
            )
            row = cur.fetchone()
    if not row:
        return {"do_not_call": False, "daily_wakeup_time": None}
    return {
        "do_not_call": row[0],
        "daily_wakeup_time": str(row[1]) if row[1] else None,
    }


def get_pending_scheduled_calls(up_to: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Return list of pending scheduled calls up to given time (default now). For scheduler use."""
    up_to = up_to or datetime.now(timezone.utc)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sc.id, sc.user_identifier, sc.scheduled_at
                FROM scheduled_calls sc
                JOIN wakeup_users u ON u.user_identifier = sc.user_identifier
                WHERE sc.status = 'pending' AND sc.scheduled_at <= %s AND u.do_not_call = FALSE
                ORDER BY sc.scheduled_at
                """,
                (up_to,),
            )
            rows = cur.fetchall()
    return [
        {"id": r[0], "user_identifier": r[1], "scheduled_at": r[2]}
        for r in rows
    ]


def mark_scheduled_call_done(scheduled_call_id: int) -> None:
    """Mark a scheduled call as completed (or failed) so it is not retried."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE scheduled_calls SET status = 'done' WHERE id = %s",
                (scheduled_call_id,),
            )
