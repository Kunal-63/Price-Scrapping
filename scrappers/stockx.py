from urllib.parse import quote
from scrapfly import ScrapflyClient, ScrapeConfig, ScrapflyScrapeError
from bs4 import BeautifulSoup
from price_parser import Price
import asyncio
import numpy as np
import re
from utils.logger import get_logger
from utils.utility import Utility
from config.scrapper_config import Config
from boto3.session import Session
from boto3.dynamodb.conditions import Attr
from models.product_item import ProductModel
from utils.antropic import AnthropicRefiner
import backoff

class StockXScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()
        self.anthropic_refiner = AnthropicRefiner()
        self.semaphore = asyncio.Semaphore(5)

        session = Session()
        dynamodb = session.resource("dynamodb", region_name="us-east-1")
        self.product_table = dynamodb.Table("ProductsTable")

    @backoff.on_exception(
        backoff.expo,
        ScrapflyScrapeError,
        max_tries=4,
        giveup=lambda e: "OPERATION_TIMEOUT" not in str(e)
    )
    async def scrape_with_retries(self, config):
        self.logger.debug(f"⏳ Scraping URL: {config.url}")
        async with self.semaphore:
            return await self.client.async_scrape(config)

    async def search(self, query, category, product_id, image_query, search_query):
        self.logger.info(f"🔍 Searching StockX for query: {query}")
        url = f"https://stockx.com/search?s={quote(query)}"

        try:
            resp = await self.scrape_with_retries(ScrapeConfig(
                url=url,
                asp=True,
                render_js=True,
                wait_for_selector='div[data-testid="Results"]',
                auto_scroll=True,
                country="US",
                session=Utility.generate_random_session(),
                proxy_pool="public_residential_pool",
            ))

            html = resp.scrape_result["content"]
            soup = BeautifulSoup(html, "html.parser")

            results = []
            for tile in soup.select('div[data-testid="ProductTile"]'):
                title_el = tile.select_one('[data-testid="product-tile-title"]')
                price_el = tile.select_one('[data-testid="product-tile-lowest-ask-amount"]')
                link_el = tile.select_one('a[data-testid="productTile-ProductSwitcherLink"]')
                image_el = tile.select_one('img[src]')

                if not title_el or not price_el or not link_el:
                    continue

                title = title_el.text.strip()
                price_text = price_el.text.strip()
                price_data = Price.fromstring(price_text)
                price_value = price_data.amount_float if price_data.amount_float else None
                if price_value is None:
                    self.logger.warning(f"⚠️ Skipping invalid price: {price_text}")
                    continue

                url_full = f"https://stockx.com{link_el['href']}"
                image_url = image_el["src"] if image_el else None
                size = self.anthropic_refiner.extract_size_from_title(title)

                location = self.anthropic_refiner.extract_location_from_title(title=title)

                base_data = {
                    "title": title,
                    "listing_price": price_value,
                    "product_id": product_id or str(np.random.randint(100000, 999999)),
                    "category": category,
                    "url": url_full,
                    "image_url": image_url,
                    "condition": "New",
                    "seller": "StockX Verified",
                    "location": location,
                    "top_rated": True,
                    "bid_count": "0",  # not available
                    "source": "stockx",
                    "image_query": image_query,
                    "search_query": search_query,
                    "size": size
                }

                results.append(base_data)

            await asyncio.gather(*[
                asyncio.to_thread(self.insert_or_update_product, item, category)
                for item in results
            ])

            self.logger.info(f"✅ StockX results fetched: {len(results)}")
            return results

        except Exception as e:
            self.logger.exception("❌ Failed to scrape StockX")
            return []

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
