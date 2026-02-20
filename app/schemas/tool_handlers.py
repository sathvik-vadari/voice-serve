"""Tool handler functions for VoiceLive function calling."""
import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Default user when no phone/user_id is provided (e.g. testing or before VAPI supplies it)
DEFAULT_USER_ID = "default_user"


# --- Perplexity search: commented out for wake-up agent; uncomment to re-enable ---
# async def search_perplexity(query: str) -> Dict[str, Any]:
#     api_key = Config.PERPLEXITY_API_KEY
#     if not api_key:
#         logger.error("PERPLEXITY_API_KEY not configured")
#         return {"error": "Perplexity API key not configured", "query": query}
#     url = "https://api.perplexity.ai/chat/completions"
#     headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
#     payload = {
#         "model": "llama-3.1-sonar-large-128k-online",
#         "messages": [{"role": "system", "content": "Be precise and concise."}, {"role": "user", "content": query}],
#         "temperature": 0.2,
#         "max_tokens": 1000,
#     }
#     try:
#         async with aiohttp.ClientSession() as session:
#             async with session.post(url, headers=headers, json=payload) as response:
#                 if response.status == 200:
#                     data = await response.json()
#                     content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
#                     return {"query": query, "result": content, "success": True}
#                 error_text = await response.text()
#                 return {"query": query, "error": f"API returned status {response.status}", "success": False}
#     except Exception as e:
#         return {"query": query, "error": str(e), "success": False}


def schedule_wakeup_call(minutes: int, user_id: Optional[str] = None) -> Dict[str, Any]:
    """Schedule a one-off wake-up call in `minutes`. Called by tool with user_id from args."""
    try:
        from app.db.wakeup import schedule_wakeup_in_minutes

        uid = user_id or DEFAULT_USER_ID
        return schedule_wakeup_in_minutes(uid, minutes)
    except Exception as e:
        logger.exception("schedule_wakeup_call failed")
        return {"success": False, "error": str(e)}


def never_call_again(user_id: Optional[str] = None) -> Dict[str, Any]:
    """Opt user out of all future wake-up calls."""
    try:
        from app.db.wakeup import set_never_call_again

        uid = user_id or DEFAULT_USER_ID
        return set_never_call_again(uid)
    except Exception as e:
        logger.exception("never_call_again failed")
        return {"success": False, "error": str(e)}


def set_daily_wakeup_time_handler(time: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    """Set or change daily wake-up time. Param name 'time' to match schema."""
    try:
        from app.db.wakeup import set_daily_wakeup_time

        uid = user_id or DEFAULT_USER_ID
        return set_daily_wakeup_time(uid, time)
    except Exception as e:
        logger.exception("set_daily_wakeup_time failed")
        return {"success": False, "error": str(e)}


# Tool registry - maps function names to actual handler functions
TOOL_HANDLERS: Dict[str, Any] = {
    "schedule_wakeup_call": lambda **kwargs: schedule_wakeup_call(
        minutes=kwargs["minutes"], user_id=kwargs.get("user_id")
    ),
    "never_call_again": lambda **kwargs: never_call_again(user_id=kwargs.get("user_id")),
    "set_daily_wakeup_time": lambda **kwargs: set_daily_wakeup_time_handler(
        time=kwargs["time"], user_id=kwargs.get("user_id")
    ),
    # "search_perplexity": search_perplexity,  # Commented out; uncomment to re-enable
}


async def execute_tool(function_name: str, arguments: str) -> Dict[str, Any]:
    """
    Execute a tool function with the given arguments.
    
    Args:
        function_name: Name of the function to call
        arguments: JSON string of function arguments
        
    Returns:
        Dictionary with function result
    """
    # Parse arguments
    try:
        args = json.loads(arguments)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse arguments for {function_name}: {e}")
        return {"error": f"Invalid JSON arguments: {e}"}
    
    # Get the handler
    if function_name not in TOOL_HANDLERS:
        logger.error(f"Unknown function: {function_name}")
        return {"error": f"Unknown function: {function_name}"}
    
    handler = TOOL_HANDLERS[function_name]
    
    # Call the handler
    try:
        import asyncio
        if asyncio.iscoroutinefunction(handler):
            result = await handler(**args)
        else:
            result = handler(**args)
        return result
    except Exception as e:
        logger.error(f"Error executing function {function_name}: {e}", exc_info=True)
        return {"error": str(e)}

