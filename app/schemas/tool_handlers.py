"""Tool handler functions for VAPI function calling (wakeup + store calls)."""
import json
import logging
import time
from typing import Dict, Any, Optional

from app.db.tickets import log_tool_call, get_store_call_by_vapi_id

logger = logging.getLogger(__name__)

DEFAULT_USER_ID = "default_user"


# ---------------------------------------------------------------------------
# Wake-up call handlers (unchanged)
# ---------------------------------------------------------------------------

def schedule_wakeup_call(minutes: int, user_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        from app.db.wakeup import schedule_wakeup_in_minutes
        from app.services.wakeup_scheduler import normalize_phone
        uid = user_id or DEFAULT_USER_ID
        if uid != DEFAULT_USER_ID:
            uid = normalize_phone(uid)
        return schedule_wakeup_in_minutes(uid, minutes)
    except Exception as e:
        logger.exception("schedule_wakeup_call failed")
        return {"success": False, "error": str(e)}


def never_call_again(user_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        from app.db.wakeup import set_never_call_again
        uid = user_id or DEFAULT_USER_ID
        return set_never_call_again(uid)
    except Exception as e:
        logger.exception("never_call_again failed")
        return {"success": False, "error": str(e)}


def set_daily_wakeup_time_handler(time_str: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        from app.db.wakeup import set_daily_wakeup_time
        uid = user_id or DEFAULT_USER_ID
        return set_daily_wakeup_time(uid, time_str)
    except Exception as e:
        logger.exception("set_daily_wakeup_time failed")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Store call handlers – called during VAPI store inquiry calls
# ---------------------------------------------------------------------------

def report_product_availability(
    product_name: str, available: bool,
    price: Optional[float] = None, notes: Optional[str] = None,
    _vapi_call_id: Optional[str] = None,
) -> Dict[str, Any]:
    result = {
        "success": True,
        "message": f"Noted: {product_name} is {'available' if available else 'not available'}."
        + (f" Price: ₹{price}" if price else ""),
    }
    _log_store_tool("report_product_availability", {
        "product_name": product_name, "available": available, "price": price, "notes": notes,
    }, result, _vapi_call_id)
    return result


def report_delivery_info(
    delivers: bool,
    eta: Optional[str] = None, delivery_mode: Optional[str] = None,
    delivery_charge: Optional[float] = None,
    _vapi_call_id: Optional[str] = None,
) -> Dict[str, Any]:
    result = {
        "success": True,
        "message": f"Noted: delivery {'available' if delivers else 'not available'}."
        + (f" ETA: {eta}" if eta else ""),
    }
    _log_store_tool("report_delivery_info", {
        "delivers": delivers, "eta": eta, "delivery_mode": delivery_mode,
        "delivery_charge": delivery_charge,
    }, result, _vapi_call_id)
    return result


def report_alternative_product(
    alternative_name: str, available: bool,
    price: Optional[float] = None, notes: Optional[str] = None,
    _vapi_call_id: Optional[str] = None,
) -> Dict[str, Any]:
    result = {
        "success": True,
        "message": f"Noted: alternative {alternative_name} is {'available' if available else 'not available'}."
        + (f" Price: ₹{price}" if price else ""),
    }
    _log_store_tool("report_alternative_product", {
        "alternative_name": alternative_name, "available": available, "price": price, "notes": notes,
    }, result, _vapi_call_id)
    return result


def _log_store_tool(tool_name: str, params: dict, result: dict, vapi_call_id: Optional[str]) -> None:
    """Persist tool call log for a store call."""
    if not vapi_call_id:
        return
    try:
        sc = get_store_call_by_vapi_id(vapi_call_id)
        if sc:
            log_tool_call(
                ticket_id=sc["ticket_id"], tool_name=tool_name,
                input_params=params, output_result=result,
                store_call_id=sc["id"],
            )
    except Exception:
        logger.exception("Failed to log store tool call")


# ---------------------------------------------------------------------------
# Tool registries
# ---------------------------------------------------------------------------

WAKEUP_TOOL_HANDLERS: Dict[str, Any] = {
    "schedule_wakeup_call": lambda **kw: schedule_wakeup_call(
        minutes=kw["minutes"], user_id=kw.get("user_id"),
    ),
    "never_call_again": lambda **kw: never_call_again(user_id=kw.get("user_id")),
    "set_daily_wakeup_time": lambda **kw: set_daily_wakeup_time_handler(
        time_str=kw["time"], user_id=kw.get("user_id"),
    ),
}

STORE_TOOL_HANDLERS: Dict[str, Any] = {
    "report_product_availability": lambda **kw: report_product_availability(
        product_name=kw["product_name"], available=kw["available"],
        price=kw.get("price"), notes=kw.get("notes"),
        _vapi_call_id=kw.get("_vapi_call_id"),
    ),
    "report_delivery_info": lambda **kw: report_delivery_info(
        delivers=kw["delivers"], eta=kw.get("eta"),
        delivery_mode=kw.get("delivery_mode"), delivery_charge=kw.get("delivery_charge"),
        _vapi_call_id=kw.get("_vapi_call_id"),
    ),
    "report_alternative_product": lambda **kw: report_alternative_product(
        alternative_name=kw["alternative_name"], available=kw["available"],
        price=kw.get("price"), notes=kw.get("notes"),
        _vapi_call_id=kw.get("_vapi_call_id"),
    ),
}

# Combined registry (backward compat)
TOOL_HANDLERS: Dict[str, Any] = {**WAKEUP_TOOL_HANDLERS, **STORE_TOOL_HANDLERS}


async def execute_tool(function_name: str, arguments: str, extra_context: dict | None = None) -> Dict[str, Any]:
    """
    Execute a tool function with the given arguments.
    extra_context can carry _vapi_call_id and similar metadata.
    """
    try:
        args = json.loads(arguments)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse arguments for %s: %s", function_name, e)
        return {"error": f"Invalid JSON arguments: {e}"}

    if extra_context:
        args.update(extra_context)

    if function_name not in TOOL_HANDLERS:
        logger.error("Unknown function: %s", function_name)
        return {"error": f"Unknown function: {function_name}"}

    handler = TOOL_HANDLERS[function_name]

    try:
        import asyncio
        if asyncio.iscoroutinefunction(handler):
            result = await handler(**args)
        else:
            result = handler(**args)
        return result
    except Exception as e:
        logger.error("Error executing function %s: %s", function_name, e, exc_info=True)
        return {"error": str(e)}
