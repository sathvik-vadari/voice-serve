"""Database layer for wake-up call preferences and scheduled calls."""
from app.db.connection import get_connection, init_db
from app.db.wakeup import (
    get_or_create_user,
    schedule_wakeup_in_minutes,
    set_never_call_again,
    set_daily_wakeup_time,
    get_user_prefs,
    get_pending_scheduled_calls,
    mark_scheduled_call_done,
)

__all__ = [
    "get_connection",
    "init_db",
    "get_or_create_user",
    "schedule_wakeup_in_minutes",
    "set_never_call_again",
    "set_daily_wakeup_time",
    "get_user_prefs",
    "get_pending_scheduled_calls",
    "mark_scheduled_call_done",
]
