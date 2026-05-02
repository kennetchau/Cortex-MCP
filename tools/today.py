from datetime import datetime, timezone


async def handle_today(request_id: str, args: dict, _tool_response, **kwargs) -> dict:
    """Get today's date and time."""
    result = datetime.now(timezone.utc).strftime("%A, %B %d, %Y, %H:%M UTC")
    return _tool_response(request_id, result)
