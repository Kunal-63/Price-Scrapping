import asyncio
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
        max_tries=4,
        giveup=lambda e: "OPERATION_TIMEOUT" not in str(e)
    )
    async def scrape_with_retries(self, config):
        self.logger.debug(f"⏳ Scraping URL: {config.url}")
        return await self.client.async_scrape(config)

    async def get_active_listings(self, query, category, product_id, image_query, search_query):
        listings = []
        sem = asyncio.Semaphore(self.MAX_CONCURRENT_DETAIL_FETCHES)

        urls = [
            f"https://www.ebay.com/sch/i.html?_nkw={quote(query)}&_sop=12&_pgn={page}"
            for page in range(1, 4)
        ]

        scrape_configs = [
            ScrapeConfig(
                url=url,
                render_js=False,
                asp=True,
                wait_for_selector=".s-item__title",
                session=Utility.generate_random_session(),
                retry=False,
                timeout=120000,
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
                        item, sem, product_id, image_query, search_query, category, listings
                    ))
            except Exception as e:
                self.logger.exception(f"❌ Failed to parse response content: {e}")
                continue

        await asyncio.gather(*tasks)
        self.logger.info(f"✅ Active listings fetched and parsed: {len(listings)}")
        return listings

    async def get_sold_items(self, query, category, product_id, image_query, search_query):
        listings = []
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
                timeout=120000,
                country="US",
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch sold listings: {e}")
            return listings

        soup = BeautifulSoup(resp.scrape_result["content"], "html.parser")
        items = soup.select(".s-item")
        tasks = [self._parse_item(item, sem, product_id, image_query, search_query, category, listings) for item in items]
        await asyncio.gather(*tasks)
        self.logger.info(f"✅ Sold listings fetched and parsed: {len(listings)}")
        return listings

    async def _parse_item(self, item, sem, product_id, image_query, search_query, category, listings):
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

                base_data = {
                    "title": title_text,
                    "product_id": product_id or str(np.random.randint(100000, 999999)),
                    "listing_price": price_value,
                    "url": detail_url,
                    "condition": item.select_one(".SECONDARY_INFO").text.strip() if item.select_one(".SECONDARY_INFO") else "Unknown",
                    "seller": (item.select_one(".s-item__seller-info-text") or item.select_one(".s-item__subtitle")).text.replace("Seller:", "").strip() if (item.select_one(".s-item__seller-info-text") or item.select_one(".s-item__subtitle")) else "Unknown",
                    "location": item.select_one(".s-item__location").text.strip() if item.select_one(".s-item__location") else "Not specified",
                    "top_rated": bool(item.select_one(".s-item__etrs-text")),
                    "source": 'ebay',
                    "image_query": image_query,
                    "search_query": search_query,
                    "size": size,
                }

                listings.append(base_data)
                self.insert_or_update_product(base_data, category)
            except Exception as e:
                self.logger.exception(f"❌ Error parsing item: {e}")

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
