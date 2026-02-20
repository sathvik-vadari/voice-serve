"""Background scheduler: when a wake-up call is due, place it via VAPI."""
import asyncio
import logging
from datetime import datetime, timezone

from app.db.wakeup import get_pending_scheduled_calls, mark_scheduled_call_done
from app.helpers.config import Config
from app.helpers.prompt_loader import PromptLoader
from app.services.vapi_client import create_phone_call

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SEC = 30
_task: asyncio.Task | None = None


def _is_phone_number(s: str) -> bool:
    """True if string looks like a callback phone number."""
    if not s or not isinstance(s, str):
        return False
    s = s.strip()
    if s in ("default_user", "default", ""):
        return False
    return s.startswith("+") or (s.isdigit() and len(s) >= 10)


async def _run_scheduler() -> None:
    """Loop: every CHECK_INTERVAL_SEC, fetch due scheduled calls and create VAPI outbound calls."""
    if not Config.VAPI_API_KEY or not Config.VAPI_PHONE_NUMBER_ID:
        logger.info("VAPI not configured; wake-up scheduler will not place outbound calls")
        return
    prompt_loader = PromptLoader()
    system_prompt = prompt_loader.get_default_prompt()
    while True:
        try:
            pending = get_pending_scheduled_calls()
            for item in pending:
                call_id = item["id"]
                user_identifier = item["user_identifier"]
                scheduled_at = item["scheduled_at"]
                if not _is_phone_number(user_identifier):
                    logger.error("Skipping scheduled call id=%s: user_identifier %r is not a phone number (callback would fail).", call_id, user_identifier)
                    mark_scheduled_call_done(call_id)
                    continue
                logger.info("Placing scheduled wake-up call: user=%s scheduled_at=%s", user_identifier, scheduled_at)
                result = await create_phone_call(user_identifier, system_prompt)
                if result.get("success"):
                    mark_scheduled_call_done(call_id)
                    logger.info("VAPI outbound call created for %s (call_id=%s)", user_identifier, call_id)
                else:
                    logger.error(
                        "VAPI wake-up call FAILED for %s - not marking done so you can retry. Error: %s Body: %s",
                        user_identifier,
                        result.get("error"),
                        result.get("body", ""),
                    )
        except Exception as e:
            logger.exception("Wake-up scheduler iteration failed: %s", e)
        await asyncio.sleep(CHECK_INTERVAL_SEC)


def start_wakeup_scheduler() -> asyncio.Task | None:
    """Start the background scheduler. Returns the task so it can be cancelled on shutdown."""
    global _task
    if _task is not None and not _task.done():
        return _task
    _task = asyncio.create_task(_run_scheduler())
    logger.info("Wake-up scheduler started (interval=%ss)", CHECK_INTERVAL_SEC)
    return _task


def stop_wakeup_scheduler() -> None:
    """Cancel the scheduler task."""
    global _task
    if _task and not _task.done():
        _task.cancel()
        _task = None
        logger.info("Wake-up scheduler stopped")
