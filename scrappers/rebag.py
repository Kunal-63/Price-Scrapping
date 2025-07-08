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
    max_tries=4,
    giveup=lambda e: "OPERATION_TIMEOUT" not in str(e)  # retry only if it's timeout
)
    async def scrape_with_retries(self, config):
        self.logger.debug(f"⏳ Scraping URL: {config.url}")
        async with self.semaphore:
            return await self.client.async_scrape(config)

    async def search(self, query, category, product_id, image_query, search_query):
        self.logger.info(f"🔍 Searching Rebag for query: {query}")
        url = f"https://shop.rebag.com/collections/all?q={quote(query)}"

        try:
            resp = await self.scrape_with_retries(ScrapeConfig(
                url=url,
                render_js=True,
                asp=True,
                wait_for_selector="div.plp__product",
                session=Utility.generate_random_session(),
                retry=False,
                timeout=120000,
                country="US"
            ))
        except Exception as e:
            self.logger.exception("❌ Rebag scraping failed")
            return []

        soup = BeautifulSoup(resp.scrape_result["content"], "html.parser")
        cards = soup.select("div.plp__product")

        results = []

        for card in cards:
            try:
                product_id_val = product_id
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

                details_tag = card.select_one(".products-carousel__card-condition")
                condition, size, alt_size = "Unknown", "Unknown", None
                if details_tag:
                    parts = [p.strip() for p in details_tag.text.split('|')]
                    if len(parts) > 0: condition = parts[0]
                    if len(parts) > 1: size = parts[1].replace("Size", "").strip()
                    if len(parts) > 2: alt_size = parts[2]

                if not condition:
                    condition = self.anthropic_refiner.extract_condition_from_title(title=title)

                if not size:
                    size = self.anthropic_refiner.extract_size_from_title(title=title)

                price_tag = card.select_one(".rewards-plus-plp__product-price-value")
                price_text = price_tag.text.strip() if price_tag else "$0"
                price = Price.fromstring(price_text).amount_float or 0.0

                base_data = {
                    "product_id": product_id or product_id_val,
                    "title": f"{brand} {title}",
                    "listing_price": price,
                    "url": item_url,
                    "image_url": image_url,
                    "condition": condition,
                    "size": size,
                    "alt_size": alt_size,
                    "seller": "Rebag",
                    "shipping": "Varies",
                    "location": "US",
                    "top_rated": False,
                    "source": "rebag",
                    "category": category,
                    "image_query": image_query,
                    "search_query": search_query,
                    "brand": brand
                }

                results.append(base_data)
                self.insert_or_update_product(base_data, category)

            except Exception as e:
                self.logger.warning(f"\u26a0\ufe0f Failed to parse product: {e}")

        self.logger.info(f"\ud83d\udce6 Parsed {len(results)} products from {url}")
        return results


    def insert_or_update_product(self, base_data: dict, category: str):
        existing = self.product_table.scan(
            FilterExpression=Attr("url").eq(base_data["url"])
        )

        product = ProductModel(base_data, category)

        if existing["Items"]:
            self.logger.info(f"🔄 Updating existing product in DB: {base_data['url']}")
        else:
            self.logger.info(f"➕ Inserting new product in DB: {base_data['url']}")

        self.product_table.put_item(Item=product.to_dict())
