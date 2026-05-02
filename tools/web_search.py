import asyncio
from ddgs import DDGS


async def handle_web_search(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """Search the live web for current information, news, or facts."""
    query = args.get("query", "")
    try:
        # Run sync DDGS in a thread to avoid blocking the event loop
        results = await asyncio.to_thread(DDGS().text, query, backend='lite', max_results=5)
        formatted = "\n\n".join([
            f"Title: {r['title']}\nURL: {r['href']}\nSnippet: {r.get('body', 'N/A')}"
            for r in results
        ])
        return _tool_response(request_id, formatted)
    except Exception as e:
        if logger:
            logger.error(f"Search failed: {e}")
        return _tool_response(request_id, f"Search failed: {str(e)}")
