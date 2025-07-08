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
        self.logger.info(f"🔍 Searching The RealReal for query: {query}")
        url = f"https://www.therealreal.com/products?keywords={quote(query)}"

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
                timeout=120000,
                retry=False
            ))
        except Exception as e:
            self.logger.exception("❌ RealReal scraping failed")
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
                condition_tag = card.select_one(".product-card__item-condition")
                image_tag = card.select_one("img.product-card-image__img--one")
                link = name_tag["href"] if name_tag and name_tag.has_attr("href") else None

                if not (brand_tag and name_tag and price_tag and link):
                    continue

                brand = brand_tag.text.strip()
                name = name_tag.text.strip()
                title = f"{brand} {name}"
                price_text = price_tag.text.strip()
                price_data = Price.fromstring(price_text)
                price = price_data.amount_float if price_data.amount_float else None
                image_url = image_tag.get("data-src") if image_tag and image_tag.has_attr("data-src") else None
                full_url = f"https://www.therealreal.com{link}"
                condition = condition_tag.text.strip() if condition_tag else None
                size = size_tag.text.strip().replace("Size:", "").strip() if size_tag else None

                # Use Gemini fallback if missing
                condition = condition or self.anthropic_refiner.extract_condition_from_title(title=title)
                size = size or self.anthropic_refiner.extract_size_from_title(title=title)

                item = {
                    "title": title,
                    "brand": brand,
                    "listing_price": price,
                    "url": full_url,
                    "image_url": image_url,
                    "condition": condition,
                    "seller": "TheRealReal Verified",
                    "shipping": "Varies (via The RealReal)",
                    "location": "US",
                    "top_rated": True,
                    "source": "therealreal",
                    "image_query": image_query,
                    "search_query": search_query,
                    "product_id": product_id or str(uuid.uuid4()),
                    "category": category,
                    "size": size,
                }

                results.append(item)
                self.insert_or_update_product(item, category)
                await asyncio.sleep(1)

            except Exception as e:
                self.logger.warning(f"⚠️ Skipping item due to parse error: {e}")

            except Exception as e:
                self.logger.warning(f"⚠️ Skipping item due to parse error: {e}")

        self.logger.info(f"✅ The RealReal Results: {len(results)} items found")
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
