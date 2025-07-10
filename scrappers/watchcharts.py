import re
from urllib.parse import quote
from scrapfly import ScrapflyClient, ScrapeConfig, ScrapflyScrapeError
from bs4 import BeautifulSoup
from utils.utility import Utility
from utils.logger import get_logger
from boto3.session import Session
from boto3.dynamodb.conditions import Attr
import asyncio
import backoff
from models.product_item import ProductModel
from price_parser import Price
from utils.antropic import AnthropicRefiner
from concurrent.futures import ThreadPoolExecutor


class WatchChartsScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

        session = Session()
        dynamodb = session.resource("dynamodb", region_name="us-east-1")
        self.product_table = dynamodb.Table("ProductsTable")

        self.scrape_semaphore = asyncio.Semaphore(5)
        self.parse_semaphore = asyncio.Semaphore(20)
        self.executor = ThreadPoolExecutor(max_workers=10)
        
        self.anthropic_refiner = AnthropicRefiner()

    @backoff.on_exception(
        backoff.expo,
        ScrapflyScrapeError,
        max_tries=2,
        giveup=lambda e: "OPERATION_TIMEOUT" not in str(e)
    )
    async def scrape_with_retries(self, config):
        self.logger.debug(f"[WatchCharts] ⏳ Scraping URL: {config.url}")
        async with self.scrape_semaphore:
            return await self.client.async_scrape(config)

    async def search(self, query, category, product_id, image_query, search_query):
        self.logger.info(f"[WatchCharts] 🔍 Searching WatchCharts for: {query}")
        url = f"https://watchcharts.com/listings?q={quote(query)}"
        results = []

        try:
            resp = await self.scrape_with_retries(ScrapeConfig(
                url=url,
                asp=True,
                render_js=False,
                session=Utility.generate_random_session(),
                proxy_pool="public_residential_pool",
                country="US",
                retry=True,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache',
                }
            ))

            soup = BeautifulSoup(resp.scrape_result["content"], "html.parser")
            anchors = soup.select("a.card-link[href^='/listing/']")

            tasks = [
                self._process_tile(anchor, category, product_id, image_query, query)
                for anchor in anchors
            ]

            parsed_items = await asyncio.gather(*tasks)
            results = [r for r in parsed_items if r]  # filter None

            self.logger.info(f"[WatchCharts] ✅ WatchCharts results fetched: {len(results)}")
            return results

        except Exception as e:
            self.logger.exception(f"[WatchCharts] ❌ WatchCharts scraping failed: {e}")
            return []

    async def _process_tile(self, anchor, category, product_id, image_query, search_query):
        async with self.parse_semaphore:
            try:
                link = f"https://watchcharts.com{anchor['href']}"
                title_el = anchor.select_one(".card-title.card-title-watch")
                price_el = anchor.select_one("h4")
                image_el = anchor.select_one("img")
                meta_el = anchor.select_one("div.font-sm")

                title = title_el.get_text(strip=True) if title_el else "Unknown"

                if not Utility.combined_similarity(title=title, query=search_query):
                    return

                if price_el:
                    price_text = price_el.get_text()
                    price_data = Price.fromstring(price_text)
                    price_value = price_data.amount_float if price_data.amount_float else None
                else:
                    price_value = None
                image_url = image_el["src"] if image_el and image_el.has_attr("src") else None
                meta = meta_el.get_text(strip=True) if meta_el else ""
                size_el = anchor.select_one("span.badge-light.font-weight-normal.text-secondary")
                size = size_el.get_text(strip=True) if size_el else "Not present"
                if size == "Not present":
                    size = Utility.extract_size_from_title(title=title)
                condition = Utility.extract_condition_from_title(title=title)
                size = Utility.refine_us_size_string(size)

                if('new' in condition.lower()):
                    condition = "New"
                elif('pre' in condition.lower()):
                    condition = "Good"
                else:
                    condition = "Varies"                
                
                seller_platform = (
                    "Chrono24" if "chrono24" in meta.lower() else
                    "WatchUSeek" if "watchuseek" in meta.lower() else
                    "Reddit" if "reddit" in meta.lower() else
                    "Forum" if "forum" in meta.lower() else
                    "Unknown "
                )

                location = Utility.extract_location_from_title(title=title)

                base_data = {
                    "title": title,
                    "listing_price": price_value,
                    "product_id": product_id,
                    "category": category,
                    "url": link,
                    "image_url": image_url,
                    "condition": condition,
                    "seller": seller_platform,
                    "shipping": "Varies",
                    "location": location,
                    "top_rated": False,
                    "source": "watchcharts",
                    "image_query": image_query,
                    "search_query": search_query,
                    'size': size,
                }

                loop = asyncio.get_event_loop()
                await loop.run_in_executor(self.executor, self.insert_or_update_product, base_data, category)
                return base_data

            except Exception as e:
                self.logger.warning(f"[WatchCharts] ⚠️ Skipping WatchCharts item due to parse error: {e}")
                return None

    def insert_or_update_product(self, base_data: dict, category: str):
        product = ProductModel(base_data, category)
        self.logger.info(f"[WatchCharts] ➕ Inserting new product in DB: {base_data['url']} WATCHCHARTS")

        self.product_table.put_item(Item=product.to_dict())
