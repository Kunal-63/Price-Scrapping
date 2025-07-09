import re
import uuid
import json
import asyncio
from urllib.parse import quote, urljoin
from scrapfly import ScrapflyClient, ScrapeConfig, ScrapflyScrapeError
from bs4 import BeautifulSoup
from utils.logger import get_logger
from utils.utility import Utility
from models.product_item import ProductModel
from price_parser import Price
from utils.antropic import AnthropicRefiner
from boto3.session import Session
from boto3.dynamodb.conditions import Attr
from config.scrapper_config import Config

import backoff

class RealRealScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

        session = Session()
        dynamodb = session.resource("dynamodb", region_name="us-east-1")
        self.product_table = dynamodb.Table("ProductsTable")
        self.shoes_table = dynamodb.Table("ShoesTable")
        self.bags_table = dynamodb.Table("BagsTable")
        self.accessories_table = dynamodb.Table("AccessoriesTable")
        self.clothing_table = dynamodb.Table("ClothingTable")
        self.beauty_table = dynamodb.Table("BeautyTable")
        self.jewelry_table = dynamodb.Table("JewelryTable")

        self.semaphore = asyncio.Semaphore(10)
        self.anthropic_refiner = AnthropicRefiner()

        self.condition_map = {
            "2988045": "As Is",
            "18": "Pristine",
            "19": "Excellent",
            "20": "Very Good",
            "30560": "Good",
            "655545": "Fair"
        }

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
        
    async def search(self, query, category, product_id, image_query, search_query):
        self.logger.info(f"🔍 Searching The RealReal for query: {query} across all conditions")

        tasks = [
            self.search_by_condition(query, category, product_id, image_query, search_query, code, label)
            for code, label in self.condition_map.items()
        ]

        all_results = await asyncio.gather(*tasks)
        flat_results = [item for sublist in all_results for item in sublist]
        return flat_results


    async def search_by_condition(self, query, category, product_id, image_query, search_query, condition_code, condition_label):
        url = f"https://www.therealreal.com/products?keywords={quote(query)}&condition%5B%5D={condition_code}"
        self.logger.info(f"🔍 Scraping RealReal: {condition_label} items for '{query}'")

        try:
            resp = await self.scrape_with_retries(ScrapeConfig(
                url=url,
                render_js=True,
                asp=True,
                wait_for_selector=".product-card-wrapper",
                rendering_stage="complete",
                session=Utility.generate_random_session(),
                country="US",
                proxy_pool="public_residential_pool",
                timeout=60000,
                retry=False
            ))
        except Exception as e:
            self.logger.error(f"❌ RealReal scrape failed for condition {condition_label}: {e}")
            return []

        html = resp.scrape_result.get("content", "")
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(".product-card-wrapper")

        results = []

        for card in items:
            try:
                brand_tag = card.select_one(".product-card__brand")
                name_tag = card.select_one(".product-card__description")
                price_tag = card.select_one(".product-card__price")
                size_tag = card.select_one(".product-card__sizes")
                image_tag = card.select_one("img.product-card-image__img--one")
                location_tag = card.select_one('.product-card__location')
                link = name_tag["href"] if name_tag and name_tag.has_attr("href") else None

                if not (brand_tag and name_tag and price_tag and link):
                    continue

                brand = brand_tag.text.strip()
                name = name_tag.text.strip()
                title = f"{brand} {name}"
                if location_tag:
                    location = location_tag.text.strip()
                price_text = price_tag.text.strip()
                price_data = Price.fromstring(price_text)
                price = price_data.amount_float if price_data.amount_float else None
                image_url = image_tag.get("data-src") if image_tag and image_tag.has_attr("data-src") else None
                full_url = f"https://www.therealreal.com{link}"
                size = size_tag.text.strip().replace("Size:", "").strip() if size_tag else None                
                size = size or self.anthropic_refiner.extract_size_from_title(title=title)
                size = Utility.refine_us_size_string(size)

                if not location_tag:
                    location = Utility.extract_location_from_title(title=title)

                if(condition_label == "Pristine"):
                    condition_label = "Like New"
                elif(condition_label == "Very Good"):
                    condition_label = "Good"
                elif(condition_label == "As Is"):
                    condition_label = "Fair"

                item = {
                    "title": title,
                    "brand": brand,
                    "listing_price": price,
                    "url": full_url,
                    "image_url": image_url,
                    "condition": condition_label,
                    "seller": "TheRealReal Verified",
                    "shipping": "Varies (via The RealReal)",
                    "location": location,
                    "top_rated": True,
                    "source": "therealreal",
                    "image_query": image_query,
                    "search_query": search_query,
                    "product_id": product_id or str(uuid.uuid4()),
                    "category": category,
                    "size": size,
                }

                results.append(item)
                # self.insert_or_update_product(item, category)

            except Exception as e:
                self.logger.warning(f"⚠️ Skipping item due to parse error: {e}")
        
        if results:
            await self.batch_insert_products(results, category)

        self.logger.info(f"✅ {condition_label}: {len(results)} items scraped for '{query}'")
        return results


    async def batch_insert_products(self, items: list[dict], category: str):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_batch_insert, items, category)

    def _sync_batch_insert(self, items: list[dict], category: str):
        with self.product_table.batch_writer(overwrite_by_pkeys=["url"]) as batch:
            for item in items:
                product = ProductModel(item, category)
                batch.put_item(Item=product.to_dict())
