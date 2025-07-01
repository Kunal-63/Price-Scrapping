import re
import json
from urllib.parse import quote
from scrapfly import ScrapflyClient, ScrapeConfig
from bs4 import BeautifulSoup
from utils.utility import Utility
from utils.logger import get_logger


class WatchChartsScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

    async def search(self, query, max_items=10):
        self.logger.info(f"🔍 Searching WatchCharts for: {query}")
        url = f"https://watchcharts.com/listings?q={quote(query)}"
        results = []

        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=url,
                asp=True,
                render_js=False,
                session=Utility.generate_random_session(),
                proxy_pool="public_residential_pool",
                country="US",
                retry=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                }
            ))

            soup = BeautifulSoup(resp.scrape_result["content"], "html.parser")

            for anchor in soup.select("a.card-link[href^='/listing/']")[:max_items]:
                try:
                    link = f"https://watchcharts.com{anchor['href']}"
                    title_el = anchor.select_one(".card-title.card-title-watch")
                    price_el = anchor.select_one("h4")
                    image_el = anchor.select_one("img")
                    meta_el = anchor.select_one("div.font-sm")

                    title = title_el.get_text(strip=True) if title_el else "Unknown"
                    price = re.sub(r"[^\d.]", "", price_el.get_text(strip=True)) if price_el else "0"
                    image_url = image_el["src"] if image_el and image_el.has_attr("src") else None
                    meta = meta_el.get_text(strip=True) if meta_el else ""

                    condition = "Used" if "used" in title.lower() else "New" if "new" in title.lower() else "Unknown"
                    seller_platform = (
                        "Chrono24" if "chrono24" in meta.lower() else
                        "WatchUSeek" if "watchuseek" in meta.lower() else
                        "Reddit" if "reddit" in meta.lower() else
                        "Forum" if "forum" in meta.lower() else
                        "Unknown"
                    )

                    item_details = await self.fetch_item_details(link)

                    results.append({
                        "title": title,
                        "listing_price": price,
                        "url": link,
                        "image_url": image_url,
                        "condition": condition,
                        "seller": seller_platform,
                        "shipping": "Varies",
                        "location": "Unknown",
                        "top_rated": False,
                        "source": "watchcharts",
                        "item_details": item_details
                    })

                except Exception as e:
                    self.logger.warning(f"⚠️ Skipping WatchCharts item due to parse error: {e}")

            self.logger.info(f"✅ WatchCharts results fetched: {len(results)}")
            return results

        except Exception as e:
            self.logger.exception(f"❌ WatchCharts scraping failed: {e}")
            return []

    async def fetch_item_details(self, url):
        self.logger.info(f"🔍 Fetching item details from: {url}")
        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=url,
                asp=True,
                render_js=True,
                wait_for_selector=".listing-info",
                rendering_wait=4000,
                session=Utility.generate_random_session(),
                proxy_pool="public_residential_pool",
                country="US",
                timeout=120000,
                retry=False
            ))

            soup = BeautifulSoup(resp.scrape_result["content"], "html.parser")
            item_details = {}

            # Description
            description_el = soup.select_one(".listing-description")
            if description_el:
                item_details["description"] = description_el.get_text(strip=True)

            # Key-value table
            for row in soup.select(".table.table-bordered tr"):
                cols = row.find_all("td")
                if len(cols) == 2:
                    key = cols[0].get_text(strip=True).lower().replace(" ", "_").replace(":", "")
                    value = cols[1].get_text(strip=True)
                    item_details[key] = value

            return item_details

        except Exception as e:
            self.logger.warning(f"⚠️ Failed to fetch item details from {url}: {e}")
            return {}
