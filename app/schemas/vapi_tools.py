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


def get_store_call_tools() -> list[dict]:
    """Return tools used during store inquiry calls."""
    return [
        {
            "type": "function",
            "function": {
                "name": "report_product_availability",
                "description": "Report whether the store has the requested product and at what price. MUST be called once you learn the answer.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "product_name": {
                            "type": "string",
                            "description": "Name of the product as confirmed by the store.",
                        },
                        "available": {
                            "type": "boolean",
                            "description": "Whether the product is currently in stock.",
                        },
                        "price": {
                            "type": "number",
                            "description": "Price in INR quoted by the store. Omit if not available.",
                        },
                        "notes": {
                            "type": "string",
                            "description": "Any extra notes (e.g. 'only 2 left', 'different color available').",
                        },
                    },
                    "required": ["product_name", "available"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "report_delivery_info",
                "description": "Report delivery details from the store. MUST be called once you learn about delivery.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "delivers": {
                            "type": "boolean",
                            "description": "Whether the store delivers to the customer's area.",
                        },
                        "eta": {
                            "type": "string",
                            "description": "Estimated delivery time (e.g. '30 minutes', '2 hours', 'next day').",
                        },
                        "delivery_mode": {
                            "type": "string",
                            "description": "Mode of delivery (e.g. 'bike', 'van', 'self-pickup', 'courier').",
                        },
                        "delivery_charge": {
                            "type": "number",
                            "description": "Delivery charge in INR. 0 if free.",
                        },
                    },
                    "required": ["delivers"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "report_alternative_product",
                "description": "Report information about an alternative product. Call this for each alternative discussed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "alternative_name": {
                            "type": "string",
                            "description": "Name of the alternative product.",
                        },
                        "available": {
                            "type": "boolean",
                            "description": "Whether the alternative is in stock.",
                        },
                        "price": {
                            "type": "number",
                            "description": "Price in INR. Omit if not available.",
                        },
                        "notes": {
                            "type": "string",
                            "description": "Any extra notes about this alternative.",
                        },
                    },
                    "required": ["alternative_name", "available"],
                },
            },
        },
    ]
