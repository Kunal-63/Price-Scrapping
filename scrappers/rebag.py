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

        self.condition_map = {
            "bc-filter-Excellent": "Excellent",
            "bc-filter-Great": "Great",
            "bc-filter-Very+Good": "Very Good",
            "bc-filter-Good": "Good",
            "bc-filter-Fair": "Fair"
        }

    async def search(self, query, category, product_id, image_query, search_query):
        self.logger.info(f"🔍 Searching Rebag for query: {query} across all conditions")

        tasks = [
            self.search_by_condition(query, category, product_id, image_query, search_query, code, label)
            for code, label in self.condition_map.items()
        ]

        all_results = await asyncio.gather(*tasks)
        flat_results = [item for sublist in all_results for item in sublist]
        return flat_results


    @backoff.on_exception(
        backoff.expo,
        ScrapflyScrapeError,
        max_tries=2,
        giveup=lambda e: "OPERATION_TIMEOUT" not in str(e)  # retry only if it's timeout
    )
    async def scrape_with_retries(self, config):
        self.logger.debug(f"⏳ Scraping URL: {config.url}")
        async with self.semaphore:
            return await self.client.async_scrape(config)

    async def search_by_condition(self, query, category, product_id, image_query, search_query, condition_code, condition_label):
        url = f"https://shop.rebag.com/collections/all?q={quote(query)}&pf_t_condition={condition_code}"
        self.logger.info(f"🔍 Scraping Rebag {condition_label} items for '{query}'")

        try:
            resp = await self.scrape_with_retries(ScrapeConfig(
                url=url,
                render_js=True,
                asp=True,
                wait_for_selector="div.plp__product",
                session=Utility.generate_random_session(),
                retry=False,
                timeout=60000,
                country="US"
            ))
        except Exception as e:
            self.logger.exception(f"❌ Rebag scraping failed for condition {condition_label}")
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

                brand_tag = card.select_one(".products-carousel__card-designer")
                brand = brand_tag.text.strip() if brand_tag else "Unknown"

                size, alt_size = "Unknown", None
                details_tag = card.select_one(".products-carousel__card-condition")
                if details_tag:
                    parts = [p.strip() for p in details_tag.text.split('|')]
                    if len(parts) > 1:
                        size = parts[1].replace("Size", "").strip()
                    if len(parts) > 2:
                        alt_size = parts[2]

                # Only fallback to Anthropic for size
                size = size or self.anthropic_refiner.extract_size_from_title(title=title)
                size = Utility.refine_us_size_string(size)

                price_tag = card.select_one(".rewards-plus-plp__product-price-value")
                price_text = price_tag.text.strip() if price_tag else "$0"
                price = Price.fromstring(price_text).amount_float or 0.0

                location = Utility.extract_location_from_title(title=title)

                if(condition_label == "Great"):
                    condition_label = "Good"
                elif(condition_label == "Very Good"):
                    condition_label = "Good"
                elif(condition_label == "Good"):
                    condition_label = "Fair"

                base_data = {
                    "product_id": product_id,
                    "title": f"{brand} {title}",
                    "listing_price": price,
                    "url": item_url,
                    "image_url": image_url,
                    "condition": condition_label,
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
                self.logger.warning(f"⚠️ Failed to parse product: {e}")

        if results:
            await self.batch_insert_products(results, category)

        self.logger.info(f"📦 Parsed {len(results)} '{condition_label}' products from Rebag")
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
            self.logger.info(f"✅ Inserted {len(items)} items in batch. REBAG")
        except Exception as e:
            self.logger.exception(f"❌ Batch insert failed: {e}")