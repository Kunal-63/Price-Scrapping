import json
from urllib.parse import quote
from scrapfly import ScrapflyClient, ScrapeConfig
from bs4 import BeautifulSoup
from utils.logger import get_logger
import asyncio

class GoatScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

    def find_all(self, data, key):
        results = []
        if isinstance(data, dict):
            for k, v in data.items():
                if k == key:
                    results.append(v)
                else:
                    results.extend(self.find_all(v, key))
        elif isinstance(data, list):
            for item in data:
                results.extend(self.find_all(item, key))
        return results

    async def search(self, query, max_items=5):
        self.logger.info(f"🔍 Searching GOAT for query: {query}")
        url = f"https://www.goat.com/search?query={quote(query)}"
        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=url,
                render_js=True,
                asp=True,
                wait_for_selector="script#__NEXT_DATA__",
                session="random",
                retry=False,
                timeout=60000,
                country="US"
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch GOAT results: {e}")
            return []

        try:
            html = resp.scrape_result["content"]
            data = json.loads(BeautifulSoup(html, "html.parser").select_one("script#__NEXT_DATA__").string)
            products = self.find_all(data, "product")
        except Exception as e:
            self.logger.exception(f"❌ Error parsing GOAT response: {e}")
            return []

        results = []
        detail_tasks = []

        for info in products[:max_items]:
            try:
                title = info.get("title") or info.get("name")
                price = info.get("price", {}).get("amount") or info.get("min_price")
                link = "https://www.goat.com" + info.get("url", "")
                image_url = info.get("media", {}).get("images", [{}])[0].get("url")
                condition = "New/Used" if info.get("product_condition") == "used" else "New"

                results.append({
                    "title": title,
                    "listing_price": str(price) if price else "0",
                    "url": link,
                    "image_url": image_url,
                    "condition": condition,
                    "seller": "GOAT Verified",
                    "shipping": "Varies (via GOAT)",
                    "location": "US (via GOAT)",
                    "top_rated": True,
                    "source": "goat"
                })

                detail_tasks.append(self.get_product_details(link))
            except Exception as e:
                self.logger.warning(f"⚠️ Skipping item due to parse error: {e}")
                detail_tasks.append(asyncio.sleep(0, result={}))

        self.logger.info(f"🧵 Fetching item details concurrently for {len(detail_tasks)} products...")
        detail_results = await asyncio.gather(*detail_tasks, return_exceptions=True)

        for item, detail in zip(results, detail_results):
            item["item_details"] = detail if isinstance(detail, dict) else {}

        self.logger.info(f"✅ GOAT results fetched: {len(results)}")
        return results

    async def get_product_details(self, product_url):
        self.logger.info(f"🔍 Fetching GOAT product details: {product_url}")
        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=product_url,
                render_js=True,
                asp=True,
                wait_for_selector="script#__NEXT_DATA__",
                session="random-detail",
                retry=False,
                timeout=60000,
                country="US"
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch GOAT detail page: {e}")
            return {}

        try:
            html = resp.scrape_result["content"]
            data = json.loads(BeautifulSoup(html, "html.parser").select_one("script#__NEXT_DATA__").string)
            product = self.find_all(data, "product")[0]  # assume 1 product per page

            # Extract description or extra info if available
            description = product.get("description") or product.get("product_type")
            release_date = product.get("release_date")
            return {
                "description": description,
                "release_date": release_date
            }
        except Exception as e:
            self.logger.warning(f"⚠️ Failed to extract details from GOAT product page: {e}")
            return {}