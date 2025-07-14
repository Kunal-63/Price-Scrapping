from urllib.parse import quote
from scrapfly import ScrapflyClient, ScrapeConfig, ScrapflyScrapeError
from bs4 import BeautifulSoup
from price_parser import Price
import asyncio
import numpy as np
import re
from config.aws import AwsConfig
from utils.logger import get_logger
from utils.utility import Utility
from config.scrapper_config import Config
from boto3.session import Session
from boto3.dynamodb.conditions import Attr
from models.product_item import ProductModel
from utils.antropic import AnthropicRefiner
import backoff
import concurrent.futures
from utils.database import is_duplicate_today

class StockXScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()
        self.anthropic_refiner = AnthropicRefiner()
        self.semaphore = asyncio.Semaphore(5)

        session = Session()
        dynamodb = session.resource("dynamodb", region_name=AwsConfig.AWS_REGION)
        self.product_table = dynamodb.Table(AwsConfig.PRODUCT_TABLE)

    @backoff.on_exception(
        backoff.expo,
        ScrapflyScrapeError,
        max_tries=2,
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
                retry=False,
                timeout=15000,
                country="US",
                session=Utility.generate_random_session(),
                proxy_pool="public_residential_pool",
                headers={
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache',
                }
            ))

            html = resp.scrape_result["content"]
            soup = BeautifulSoup(html, "html.parser")

            results = []
            for tile in soup.select('div[data-testid="ProductTile"]')[:5]:
                link_el = tile.select_one('a[data-testid="productTile-ProductSwitcherLink"]')
                url_full = f"https://stockx.com{link_el['href']}"
                if is_duplicate_today(url=url_full):
                    continue
 
                title_el = tile.select_one('[data-testid="product-tile-title"]')
                price_el = tile.select_one('[data-testid="product-tile-lowest-ask-amount"]')
                image_el = tile.select_one('img[src]')

                if not title_el or not price_el or not link_el:
                    continue

                title = title_el.text.strip()
                if not Utility.combined_similarity(title=title, query=query, threshold=0.7):
                    continue
                
                price_text = price_el.text.strip()
                price_data = Price.fromstring(price_text)
                price_value = price_data.amount_float if price_data.amount_float else None
                if price_value is None:
                    self.logger.warning(f"⚠️ Skipping invalid price: {price_text}")
                    continue

                image_url = image_el["src"] if image_el else None
                size = Utility.extract_size_from_title(title)
                size = Utility.refine_us_size_string(size)

                location = Utility.extract_location_from_title(title=title)

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

            await asyncio.to_thread(self.bulk_insert_products, results, category)

            self.logger.info(f"[StockX] ✅ StockX results fetched: {len(results)} STOCKX")
            return results

        except Exception as e:
            self.logger.exception("[StockX] ❌ Failed to scrape StockX")
            return []

    def bulk_insert_products(self, items, category):
        self.logger.info(f"[StockX] 🚀 Bulk inserting {len(items)} StockX products")
        with self.product_table.batch_writer(overwrite_by_pkeys=["url"]) as batch:
            for item in items:
                product = ProductModel(item, category)
                batch.put_item(Item=product.to_dict())
