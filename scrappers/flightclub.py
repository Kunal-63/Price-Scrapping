import re
import json
from urllib.parse import quote
from scrapfly import ScrapflyClient, ScrapeConfig
from bs4 import BeautifulSoup
from utils.logger import get_logger

class FlightClubScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

    async def search(self, query, max_items=5):
        self.logger.info(f"🔍 Searching Flight Club for query: {query}")
        url = f"https://www.flightclub.com/catalogsearch/result/?q={quote(query)}"
        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=url,
                render_js=True,
                asp=True,
                wait_for_selector="script",
                session="random",
                retry=False,
                timeout=60000,
                country="US"
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch Flight Club results: {e}")
            return []

        html = resp.scrape_result["content"]
        match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*});', html)
        if not match:
            self.logger.error("❌ Could not extract JSON from Flight Club.")
            return []

        try:
            data = json.loads(match.group(1))
            products = data.get("search", {}).get("products", {}).get("hits", [])[:max_items]
        except Exception as e:
            self.logger.exception(f"❌ Failed to parse Flight Club JSON: {e}")
            return []

        results = []
        for p in products:
            try:
                title = p.get("name")
                price = p.get("price_cents", {}).get("usd", 0) // 100
                url = "https://www.flightclub.com" + p.get("url", "")
                image_url = p.get("image_url", "")

                results.append({
                    "title": title,
                    "listing_price": str(price),
                    "url": url,
                    "image_url": image_url,
                    "condition": "New",  # Flight Club primarily sells new items
                    "seller": "Flight Club Verified",
                    "shipping": "Varies (via Flight Club)",
                    "location": "US (via Flight Club)",
                    "top_rated": True,
                    "source": "flightclub"
                })
            except Exception as e:
                self.logger.warning(f"⚠️ Skipping item due to error: {e}")

        self.logger.info(f"✅ Flight Club results fetched: {len(results)}")
        return results
