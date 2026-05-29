"""
Scrapes external websites linked from LinkedIn profiles.
Uses trafilatura — free, no API key, works well on most sites.
"""

import httpx
import trafilatura
from loguru import logger


async def scrape_website(url: str, timeout: int = 10) -> str:
    """
    Fetch and extract clean text from a URL.
    Returns plain text content, or empty string on failure.
    """
    if not url or not url.startswith("http"):
        return ""

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; research-bot/1.0; "
                    "+https://example.com/bot)"
                ),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text

        # Extract readable text with trafilatura
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
            favor_precision=True,
        )

        if not text:
            return ""

        # Trim to ~3000 chars for AI processing
        text = text.strip()
        if len(text) > 3000:
            text = text[:3000] + "..."

        logger.info(f"Scraped {url} → {len(text)} chars")
        return text

    except httpx.HTTPStatusError as e:
        logger.warning(f"HTTP {e.response.status_code} for {url}")
        return ""
    except httpx.TimeoutException:
        logger.warning(f"Timeout scraping {url}")
        return ""
    except Exception as e:
        logger.warning(f"Scrape failed for {url}: {e}")
        return ""
