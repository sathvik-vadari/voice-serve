#!/usr/bin/env python3
"""Set a scheduled call back to pending so the scheduler will retry. Usage: python -m app.scripts.retry_scheduled_call [scheduled_call_id]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(".env")

from app.db.connection import get_connection

def main():
    call_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
    if not call_id:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, user_identifier, scheduled_at, status FROM scheduled_calls ORDER BY id DESC LIMIT 5")
                rows = cur.fetchall()
        print("Usage: python -m app.scripts.retry_scheduled_call <id>")
        print("Recent scheduled_calls:")
        for r in rows:
            print(f"  id={r[0]} user={r[1]} at={r[2]} status={r[3]}")
        return
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE scheduled_calls SET status = 'pending' WHERE id = %s RETURNING id", (call_id,))
            if cur.fetchone():
                print(f"Set scheduled_call id={call_id} back to pending. Scheduler will retry within ~30s.")
            else:
                print(f"No row with id={call_id}")

if __name__ == "__main__":
    main()
