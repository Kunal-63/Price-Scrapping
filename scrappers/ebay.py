import asyncio
from concurrent.futures import ThreadPoolExecutor
from scrapfly import ScrapflyClient, ScrapeConfig, ScrapflyScrapeError
from bs4 import BeautifulSoup
from urllib.parse import quote
import re
import numpy as np
from price_parser import Price
from utils.logger import get_logger
from utils.utility import Utility
from models.product_item import ProductModel
from boto3.session import Session
from boto3.dynamodb.conditions import Attr
from config.scrapper_config import Config
from utils.antropic import AnthropicRefiner
import backoff

class EbayScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()
        self.MAX_CONCURRENT_DETAIL_FETCHES = 20
        self.executor = ThreadPoolExecutor(max_workers=10)

        session = Session()
        dynamodb = session.resource("dynamodb", region_name="us-east-1")
        self.product_table = dynamodb.Table("ProductsTable")
        self.anthropic_refiner = AnthropicRefiner()

    async def search(self, query, category, product_id, image_query, search_query):
        if not category:
            raise ValueError("Category must be specified for eBay search.")
        self.logger.info(f"🔍 Starting eBay search for: {query}")
        try:
            active, sold = await asyncio.gather(
                self.get_active_listings(query, category, product_id, image_query, search_query),
                self.get_sold_items(query, category, product_id, image_query, search_query)
            )
            return {'active': active, 'sold': sold}
        except Exception as e:
            self.logger.exception(f"❌ Error during eBay search: {e}")
            return {'active': [], 'sold': []}

    @backoff.on_exception(
        backoff.expo,
        ScrapflyScrapeError,
        max_tries=2,
        giveup=lambda e: "OPERATION_TIMEOUT" not in str(e)
    )
    async def scrape_with_retries(self, config):
        self.logger.debug(f"⏳ Scraping URL: {config.url}")
        return await self.client.async_scrape(config)

    async def get_active_listings(self, query, category, product_id, image_query, search_query):
        listings = []
        products_to_insert = []
        sem = asyncio.Semaphore(self.MAX_CONCURRENT_DETAIL_FETCHES)

        urls = [
            f"https://www.ebay.com/sch/i.html?_nkw={quote(query)}&_sop=12&_pgn={page}"
            for page in range(1, 4)
        ]

        scrape_configs = [
            ScrapeConfig(
                url=url,
                render_js=True,
                asp=True,
                wait_for_selector=".s-item__title",
                session=Utility.generate_random_session(),
                retry=False,
                timeout=60000,
                country="US",
            )
            for url in urls
        ]

        self.logger.debug(f"🚀 Fetching active listings concurrently from {len(urls)} pages.")
        try:
            responses = await asyncio.gather(
                *(self.scrape_with_retries(cfg) for cfg in scrape_configs),
                return_exceptions=True
            )
        except Exception as e:
            self.logger.exception(f"❌ Error during concurrent page scraping: {e}")
            return listings

        tasks = []
        for response in responses:
            if isinstance(response, Exception):
                self.logger.warning(f"⚠️ Skipping failed page response: {response}")
                continue

            try:
                soup = BeautifulSoup(response.scrape_result["content"], "html.parser")
                items = soup.select(".s-item")
                for item in items:
                    tasks.append(self._parse_item(
                        item, sem, product_id, image_query, search_query,
                        category, listings, products_to_insert
                    ))
            except Exception as e:
                self.logger.exception(f"❌ Failed to parse response content: {e}")
                continue

        await asyncio.gather(*tasks)
        await asyncio.get_event_loop().run_in_executor(
            self.executor, self.bulk_insert_products, products_to_insert
        )

        self.logger.info(f"✅ Active listings fetched and parsed: {len(listings)}")
        return listings

    async def get_sold_items(self, query, category, product_id, image_query, search_query):
        listings = []
        products_to_insert = []
        sem = asyncio.Semaphore(self.MAX_CONCURRENT_DETAIL_FETCHES)

        url = f"https://www.ebay.com/sch/i.html?_nkw={quote(query)}&_sop=13&LH_Complete=1&LH_Sold=1"
        self.logger.debug(f"📱 Fetching sold listings: {url}")

        try:
            resp = await self.scrape_with_retries(ScrapeConfig(
                url=url,
                render_js=True,
                asp=True,
                wait_for_selector=".s-item__title",
                session=Utility.generate_random_session(),
                retry=False,
                timeout=60000,
                country="US",
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch sold listings: {e}")
            return listings

        soup = BeautifulSoup(resp.scrape_result["content"], "html.parser")
        items = soup.select(".s-item")

        tasks = [
            self._parse_item(item, sem, product_id, image_query, search_query, category, listings, products_to_insert)
            for item in items
        ]

        await asyncio.gather(*tasks)
        await asyncio.get_event_loop().run_in_executor(
            self.executor, self.bulk_insert_products, products_to_insert
        )

        self.logger.info(f"✅ Sold listings fetched and parsed: {len(listings)}")
        return listings

    async def _parse_item(self, item, sem, product_id, image_query, search_query, category, listings, products_to_insert):
        async with sem:
            try:
                title_el = item.select_one(".s-item__title")
                if not title_el:
                    return

                title_text = title_el.get_text(strip=True)
                if not title_text or title_text.lower().startswith("shop on ebay"):
                    return

                link_el = item.select_one("a.s-item__link")
                price_el = item.select_one(".s-item__price")

                if not link_el:
                    return

                price_text = price_el.get_text()
                price_data = Price.fromstring(price_text)
                price_value = price_data.amount_float if price_data.amount_float else None

                detail_url = link_el['href']
                size = self.anthropic_refiner.extract_size_from_title(title_text)
                size = Utility.refine_us_size_string(size)
                location = self.anthropic_refiner.refine_location(item.select_one(".s-item__location").text.strip() if item.select_one(".s-item__location") else "Not specified")

                condition = item.select_one(".SECONDARY_INFO").text.strip() if item.select_one(".SECONDARY_INFO") else "Unknown"
                if('new' in condition.lower()):
                    condition = "New"
                elif('pre' in condition.lower()):
                    condition = "Good"
                else:
                    condition = "Varies"

                base_data = {
                    "title": title_text,
                    "product_id": product_id or str(np.random.randint(100000, 999999)),
                    "listing_price": price_value,
                    "url": detail_url,
                    "condition": condition,
                    "seller": (item.select_one(".s-item__seller-info-text") or item.select_one(".s-item__subtitle")).text.replace("Seller:", "").strip() if (item.select_one(".s-item__seller-info-text") or item.select_one(".s-item__subtitle")) else "Unknown",
                    "location": location,
                    "top_rated": bool(item.select_one(".s-item__etrs-text")),
                    "source": 'ebay',
                    "image_query": image_query,
                    "search_query": search_query,
                    "size": size,
                }

                product = ProductModel(base_data, category)
                products_to_insert.append(product.to_dict())
                listings.append(base_data)
            except Exception as e:
                self.logger.exception(f"❌ Error parsing item: {e}")

    def bulk_insert_products(self, items: list):
        self.logger.info("INSERTING IN EBAY")
        if not items:
            return

        def write_batch(batch):
            try:
                with self.product_table.batch_writer(overwrite_by_pkeys=["url"]) as writer:
                    for item in batch:
                        writer.put_item(Item=item)
                self.logger.info(f"✅ Inserted batch of {len(batch)} items.")
            except Exception as e:
                self.logger.exception(f"❌ Batch insert failed: {e}")

        def chunks(data, size=25):
            for i in range(0, len(data), size):
                yield data[i:i + size]

        batches = list(chunks(items, 25))

        futures = [self.executor.submit(write_batch, batch) for batch in batches]

        for future in futures:
            future.result()