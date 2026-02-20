"""Background scheduler: when a wake-up call is due, place it via VAPI."""
import asyncio
import re

from app.db.wakeup import get_pending_scheduled_calls, mark_scheduled_call_done
from app.helpers.config import Config
from app.helpers.logger import setup_logger
from app.helpers.prompt_loader import PromptLoader
from app.services.vapi_client import create_phone_call

logger = setup_logger(__name__)

CHECK_INTERVAL_SEC = 30
_task: asyncio.Task | None = None


def normalize_phone(number: str) -> str:
    """Ensure the number is in international format (+91... for Indian numbers)."""
    n = re.sub(r"[\s\-\(\)]", "", number.strip())
    if n.startswith("+"):
        return n
    # Bare 10-digit Indian mobile number
    if n.isdigit() and len(n) == 10:
        return f"+91{n}"
    # 0-prefixed Indian number
    if n.startswith("0") and len(n) == 11:
        return f"+91{n[1:]}"
    return n


def _is_phone_number(s: str) -> bool:
    if not s or not isinstance(s, str):
        return False
    s = s.strip()
    if s in ("default_user", "default", ""):
        return False
    return s.startswith("+") or (s.isdigit() and len(s) >= 10)


async def _run_scheduler() -> None:
    if not Config.VAPI_API_KEY or not Config.VAPI_PHONE_NUMBER_ID:
        logger.info("VAPI not configured; wake-up scheduler will not place outbound calls")
        return
    prompt_loader = PromptLoader()
    system_prompt = prompt_loader.get_default_prompt()

    logger.info("Scheduler loop started (checking every %ds)", CHECK_INTERVAL_SEC)

    while True:
        try:
            pending = get_pending_scheduled_calls()
            if pending:
                logger.info("Scheduler found %d pending call(s)", len(pending))
            for item in pending:
                call_id = item["id"]
                user_identifier = item["user_identifier"]
                scheduled_at = item["scheduled_at"]

                if not _is_phone_number(user_identifier):
                    logger.error(
                        "Skipping call id=%s: %r is not a valid phone number",
                        call_id, user_identifier,
                    )
                    mark_scheduled_call_done(call_id)
                    continue

                phone = normalize_phone(user_identifier)
                logger.info("Placing scheduled call id=%s to %s (scheduled_at=%s)", call_id, phone, scheduled_at)

                result = await create_phone_call(phone, system_prompt)
                if result.get("success"):
                    mark_scheduled_call_done(call_id)
                    logger.info("VAPI call placed for %s (call_id=%s)", phone, call_id)
                else:
                    logger.error(
                        "VAPI call FAILED for %s (call_id=%s): %s | %s",
                        phone, call_id, result.get("error"), result.get("body", ""),
                    )
                    # Mark done even on failure to avoid infinite retry loop
                    mark_scheduled_call_done(call_id)

        except Exception as e:
            logger.exception("Scheduler iteration error: %s", e)
        await asyncio.sleep(CHECK_INTERVAL_SEC)


def start_wakeup_scheduler() -> asyncio.Task | None:
    global _task
    if _task is not None and not _task.done():
        return _task
    _task = asyncio.create_task(_run_scheduler())
    logger.info("Wake-up scheduler started (interval=%ss)", CHECK_INTERVAL_SEC)
    return _task


def stop_wakeup_scheduler() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        _task = None
        logger.info("Wake-up scheduler stopped")
