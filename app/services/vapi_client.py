"""VAPI API client for outbound phone calls (wake-up + store inquiry)."""
import logging
from typing import Any, Optional

import aiohttp

from app.helpers.config import Config
from app.schemas.vapi_tools import get_vapi_wakeup_tools, get_store_call_tools

logger = logging.getLogger(__name__)

VAPI_CALL_PHONE_URL = "https://api.vapi.ai/call/phone"


# ---------------------------------------------------------------------------
# Wake-up call assistant (existing)
# ---------------------------------------------------------------------------

def _get_wakeup_assistant(server_url: str, system_prompt: str, *, include_server_url: bool = True) -> dict[str, Any]:
    base = (server_url or "").rstrip("/")
    webhook_url = f"{base}/api/vapi/webhook" if base else ""
    voice_config: Any = Config.VAPI_VOICE
    if Config.VAPI_VOICE_PROVIDER:
        voice_config = {"provider": Config.VAPI_VOICE_PROVIDER, "voiceId": Config.VAPI_VOICE}
    assistant: dict[str, Any] = {
        "firstMessage": "Hi, this is your wake-up call. How can I help you today?",
        "model": {
            "provider": Config.VAPI_MODEL_PROVIDER,
            "model": Config.VAPI_MODEL,
            "messages": [{"role": "system", "content": system_prompt}],
            "functions": [t["function"] for t in get_vapi_wakeup_tools()],
        },
        "voice": voice_config,
    }
    if include_server_url and webhook_url:
        assistant["serverUrl"] = webhook_url
    elif include_server_url and not base:
        logger.warning(
            "VAPI_SERVER_URL is not set: outbound calls will not have tool-calling. "
            "Set VAPI_SERVER_URL to a public URL (e.g. ngrok) so VAPI can reach your webhook."
        )
    return assistant


def get_wakeup_assistant_for_webhook(server_url: str, system_prompt: str) -> dict[str, Any]:
    return _get_wakeup_assistant(server_url, system_prompt)


# ---------------------------------------------------------------------------
# Store inquiry assistant (new)
# ---------------------------------------------------------------------------

def _get_store_assistant(server_url: str, system_prompt: str) -> dict[str, Any]:
    base = (server_url or "").rstrip("/")
    webhook_url = f"{base}/api/vapi/store-webhook" if base else ""
    voice_config: Any = Config.VAPI_VOICE
    if Config.VAPI_VOICE_PROVIDER:
        voice_config = {"provider": Config.VAPI_VOICE_PROVIDER, "voiceId": Config.VAPI_VOICE}
    assistant: dict[str, Any] = {
        "firstMessage": "Hello, I'm calling to check if you have a product in stock. Do you have a moment?",
        "model": {
            "provider": Config.VAPI_MODEL_PROVIDER,
            "model": Config.VAPI_MODEL,
            "messages": [{"role": "system", "content": system_prompt}],
            "functions": [t["function"] for t in get_store_call_tools()],
        },
        "voice": voice_config,
    }
    if webhook_url:
        assistant["serverUrl"] = webhook_url
    return assistant


# ---------------------------------------------------------------------------
# Outbound call creators
# ---------------------------------------------------------------------------

async def _place_call(assistant: dict, customer_number: str) -> dict[str, Any]:
    api_key = Config.VAPI_API_KEY
    phone_number_id = Config.VAPI_PHONE_NUMBER_ID
    if not api_key:
        return {"success": False, "error": "VAPI_API_KEY not configured"}
    if not phone_number_id:
        return {"success": False, "error": "VAPI_PHONE_NUMBER_ID not configured"}

    payload = {
        "assistant": assistant,
        "phoneNumberId": phone_number_id,
        "customer": {"number": customer_number},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(VAPI_CALL_PHONE_URL, headers=headers, json=payload) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    logger.info("VAPI call created: customer=%s", customer_number)
                    return {"success": True, "call": data}
                text = await resp.text()
                logger.error("VAPI call failed: %s %s", resp.status, text)
                return {"success": False, "error": f"VAPI returned {resp.status}", "body": text}
    except Exception as e:
        logger.exception("VAPI call failed")
        return {"success": False, "error": str(e)}


async def create_phone_call(
    customer_number: str,
    system_prompt: str,
    *,
    assistant_overrides: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Create an outbound wake-up phone call."""
    server_url = Config.VAPI_SERVER_URL or ""
    assistant = _get_wakeup_assistant(server_url, system_prompt, include_server_url=bool(server_url))
    if assistant_overrides:
        assistant.update({k: v for k, v in assistant_overrides.items() if v is not None})
    return await _place_call(assistant, customer_number)


async def create_store_phone_call(
    customer_number: str,
    system_prompt: str,
    ticket_id: str,
    store_call_id: int,
) -> dict[str, Any]:
    """Create an outbound store inquiry phone call."""
    server_url = Config.VAPI_SERVER_URL or ""
    assistant = _get_store_assistant(server_url, system_prompt)
    return await _place_call(assistant, customer_number)
