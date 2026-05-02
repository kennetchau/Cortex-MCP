async def handle_add(request_id: str, args: dict, _tool_response, **kwargs) -> dict:
    """Adds two numbers together."""
    result = args.get("a", 0) + args.get("b", 0)
    return _tool_response(request_id, str(result))
