"""OpenAI-style tool definitions for VAPI (function calling over phone)."""


def get_vapi_wakeup_tools() -> list[dict]:
    """Return wake-up call tools in OpenAI/VAPI function format."""
    return [
        {
            "type": "function",
            "function": {
                "name": "schedule_wakeup_call",
                "description": "Schedule a wake-up call in a given number of minutes. Use when the user asks to be called back in X minutes or to set a one-time wake-up.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "minutes": {
                            "type": "integer",
                            "description": "Number of minutes from now when the user should receive the wake-up call.",
                            "minimum": 1,
                        },
                        "user_id": {
                            "type": "string",
                            "description": "Optional. User identifier (e.g. phone number). Omit to use the caller's number.",
                        },
                    },
                    "required": ["minutes"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "never_call_again",
                "description": "Stop all wake-up calls for this user. Use when they say they never want to be called again or to unsubscribe.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "Optional. User identifier. Omit to use the caller's number.",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "set_daily_wakeup_time",
                "description": "Set or change the daily wake-up call time. Use when the user wants to change what time they get their regular wake-up call.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "time": {
                            "type": "string",
                            "description": "Time of day, e.g. '7:30', '7:30 AM', '19:30'.",
                        },
                        "user_id": {
                            "type": "string",
                            "description": "Optional. User identifier. Omit to use the caller's number.",
                        },
                    },
                    "required": ["time"],
                },
            },
        },
    ]
