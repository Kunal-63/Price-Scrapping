#NO ITEM DETAILS FOUND
import random
import re
from urllib.parse import quote
from scrapfly import ScrapflyClient, ScrapeConfig, ScrapflyScrapeError
from bs4 import BeautifulSoup
from utils.utility import Utility
from utils.logger import get_logger
import asyncio
from boto3.session import Session
from boto3.dynamodb.conditions import Attr
from models.product_item import ProductModel
from config.scrapper_config import Config
from price_parser.parser import Price
import backoff
from utils.antropic import AnthropicRefiner

class RebagScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

        session = Session()
        dynamodb = session.resource("dynamodb", region_name="us-east-1")
        self.product_table = dynamodb.Table("ProductsTable")
        
        self.semaphore = asyncio.Semaphore(10)
        self.anthropic_refiner = AnthropicRefiner()

    @backoff.on_exception(
        backoff.expo,
        ScrapflyScrapeError,
        max_tries=2,
        giveup=lambda e: "OPERATION_TIMEOUT" not in str(e)
    )
    async def scrape_with_retries(self, config):
        self.logger.debug(f"[Rebag] ⏳ Scraping URL: {config.url}")
        async with self.semaphore:
            return await self.client.async_scrape(config)

    async def search(self, query, category, product_id, image_query, search_query):
        url = f"https://shop.rebag.com/collections/all?q={quote(query)}"
        self.logger.info(f"[Rebag] 🔍 Scraping Rebag {url} items for '{query}'")

        try:
            resp = await self.scrape_with_retries(ScrapeConfig(
                url=url,
                session=Utility.generate_random_session(),
                retry=False,
                timeout=30000,
                asp=True,
                render_js=True,
                wait_for_selector="div.plp__product",
                country="US",
                headers={
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache',
                }
            ))
        except Exception as e:
            self.logger.exception(f"[Rebag] ❌ Rebag scraping failed for condition {query}")
            return []

        soup = BeautifulSoup(resp.scrape_result["content"], "html.parser")
        cards = soup.select("div.plp__product")

        results = []

        for card in cards:
            try:
                link_tag = card.select_one("a.plp__card")
                if not link_tag:
                    continue
                item_url = link_tag.get("href", "").strip()

                image_tag = card.select_one("img")
                image_url = image_tag.get("src") if image_tag else None

                title_tag = card.select_one(".products-carousel__card-title")
                title = title_tag.text.strip() if title_tag else "Unknown"
                if not Utility.combined_similarity(title=title, query=query, threshold=0.7):
                    continue

                brand_tag = card.select_one(".products-carousel__card-designer")
                brand = brand_tag.text.strip() if brand_tag else "Unknown"

                size, alt_size = "Unknown", None
                details_tag = card.select_one(".products-carousel__card-condition")
                condition = "Unknown"
                if details_tag:
                    parts = [p.strip() for p in details_tag.text.split('|')]
                    if len(parts) > 1:
                        size = parts[1].replace("Size", "").strip()
                    if len(parts) > 2:
                        alt_size = parts[2]
                    if len(parts) > 0:
                        condition = parts[0].strip()

                size = size or Utility.extract_size_from_title(title=title)
                size = Utility.refine_us_size_string(size)

                price_tag = card.select_one(".rewards-plus-plp__product-price-value")
                price_text = price_tag.text.strip() if price_tag else "$0"
                price = Price.fromstring(price_text).amount_float or 0.0

                location = Utility.extract_location_from_title(title=title)

                if(condition == "Great"):
                    condition = "Good"
                elif(condition == "Very Good"):
                    condition = "Good"
                elif(condition == "Good"):
                    condition = "Fair"

                base_data = {
                    "product_id": product_id,
                    "title": f"{brand} {title}",
                    "listing_price": price,
                    "url": item_url,
                    "image_url": image_url,
                    "condition": condition,
                    "size": size,
                    "alt_size": alt_size,
                    "seller": "Rebag",
                    "shipping": "Varies",
                    "location": location,
                    "top_rated": False,
                    "source": "rebag",
                    "category": category,
                    "image_query": image_query,
                    "search_query": search_query,
                    "brand": brand
                }

                results.append(base_data)
                # self.insert_or_update_product(base_data, category)

            except Exception as e:
                self.logger.warning(f"[Rebag] ⚠️ Failed to parse product: {e}")

        if results:
            await self.batch_insert_products(results, category)

        self.logger.info(f"[Rebag] 📦 Parsed {len(results)} '{query}' products from Rebag")
        return results

    async def batch_insert_products(self, items: list[dict], category: str):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_batch_insert, items, category)

    def _sync_batch_insert(self, items: list[dict], category: str):
        try:
            with self.product_table.batch_writer(overwrite_by_pkeys=["url"]) as batch:
                for item in items:
                    product = ProductModel(item, category)
                    batch.put_item(Item=product.to_dict())
            self.logger.info(f"[Rebag] ✅ Inserted {len(items)} items in batch. REBAG")
        except Exception as e:
            self.logger.exception(f"[Rebag] ❌ Batch insert failed: {e}")