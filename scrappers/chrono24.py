import json
from urllib.parse import quote
from scrapfly import ScrapflyClient, ScrapeConfig
from bs4 import BeautifulSoup
from utils.logger import get_logger
import asyncio
from utils.utility import Utility

class Chrono24Scraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

    async def search(self, query):
        url = f"https://www.chrono24.com/search/index.htm?dosearch=true&query={quote(query)}"
        self.logger.info(f"📡 Fetching Chrono24 results for query: {query}")

        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=url,
                asp=True,
                render_js=False,
                country="US",
                session=Utility.generate_random_session(),
                proxy_pool="public_residential_pool",
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    )
                }
            ))
        except Exception as e:
            self.logger.exception(f"❌ Chrono24 scraping failed: {e}")
            return []

        html = resp.scrape_result.get("content", "")
        soup = BeautifulSoup(html, "html.parser")

        results = []
        detail_tasks = []

        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(script.string)
                if isinstance(data, dict) and "@graph" in data:
                    for node in data["@graph"]:
                        if node.get("@type") == "AggregateOffer" and "offers" in node:
                            for offer in node["offers"]:
                                product_url = offer.get("url", "").strip()
                                item = {
                                    "title": offer.get("name", "").strip(),
                                    "price": str(offer.get("price", "")).strip(),
                                    "url": product_url,
                                    "image": (
                                        offer.get("image", {}).get("contentUrl", "")
                                        if isinstance(offer.get("image"), dict)
                                        else offer.get("image", "")
                                    ),
                                    "site": "Chrono24"
                                }

                                results.append(item)
                                if product_url:
                                    detail_tasks.append(self.get_product_detail(product_url))
                                else:
                                    detail_tasks.append(asyncio.sleep(0, result={}))  # fallback
            except json.JSONDecodeError:
                self.logger.warning("⚠️ Skipped invalid JSON script tag")

        self.logger.info(f"🧵 Fetching details for {len(detail_tasks)} items concurrently...")

        # Concurrently fetch product details
        details_list = await asyncio.gather(*detail_tasks, return_exceptions=True)

        for item, detail in zip(results, details_list):
            item["item_details"] = detail if isinstance(detail, dict) else {}

        self.logger.info(f"✅ Found {len(results)} Chrono24 items with details")
        return results

    async def get_product_detail(self, product_url):
        self.logger.info(f"🔍 Fetching product detail from: {product_url}")

        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=product_url,
                asp=True,
                render_js=False,
                country="US",
                session=Utility.generate_random_session(),
                proxy_pool="public_residential_pool",
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    )
                }
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch product detail: {e}")
            return {}

        html = resp.scrape_result.get("content", "")
        return self.extract_item_details(html)

    def extract_item_details(self, html: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        item_details = {}

        table = soup.find("table")
        if not table:
            return item_details

        # Go through all <tr> rows in the table
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) == 2:
                key = tds[0].get_text(strip=True).replace(":", "")
                value = tds[1].get_text(" ", strip=True)
                item_details[key] = value
            elif len(tds) == 1 and tds[0].get("colspan") == "2":
                # Optional: you can also track sections like "Case", "Bracelet/strap"
                section_title = tds[0].find("h3")
                if section_title:
                    current_section = section_title.get_text(strip=True)
            else:
                continue

        return item_details
