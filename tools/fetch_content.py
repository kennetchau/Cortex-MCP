import httpx
import trafilatura
from urllib.parse import urlparse


async def scrape_and_summarize(url: str, max_words: int = 1000):
    """Helper function to scrape and summarize a URL."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive"
        }

        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            response = await client.get(url)
            extracted = trafilatura.extract(response.text)

            if not extracted:
                return f"No readable content found in {url}"

            words = extracted.split()
            if len(words) > max_words:
                extracted = " ".join(words[:max_words]) + "... (content truncated)"

            return f"URL: {url}\nContent:\n{extracted}"
    except Exception as e:
        return f"Error fetching {url}: {str(e)}"


async def handle_fetch_content(request_id: str, args: dict, _tool_response, **kwargs) -> dict:
    """Scrape a URL, extract main content, and return a clean text summary."""
    url = args.get("url", "")
    parsed = urlparse(url)
    if not all([parsed.scheme, parsed.netloc]):
        return _tool_response(request_id, "Invalid URL format.")

    result_text = await scrape_and_summarize(url)
    return _tool_response(request_id, result_text)
