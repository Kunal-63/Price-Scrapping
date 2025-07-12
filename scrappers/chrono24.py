import json
import asyncio
import uuid
from urllib.parse import quote, urlparse, parse_qs
from bs4 import BeautifulSoup
from scrapfly import ScrapflyClient, ScrapeConfig, ScrapflyScrapeError
from boto3.session import Session
from boto3.dynamodb.conditions import Key
import backoff
from concurrent.futures import ThreadPoolExecutor
from price_parser import Price
from utils.logger import get_logger
from utils.utility import Utility
from utils.antropic import AnthropicRefiner
from models.product_item import ProductModel
from config.scrapper_config import Config
from utils.database import is_duplicate_today

class Chrono24Scraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()
        self.anthropic_refiner = AnthropicRefiner()
        self.semaphore = asyncio.Semaphore(10)

        session = Session()
        dynamodb = session.resource("dynamodb", region_name="us-east-1")
        self.product_table = dynamodb.Table("ProductsTable")
        self.executor = ThreadPoolExecutor(max_workers=8)


    @backoff.on_exception(
        backoff.expo,
        ScrapflyScrapeError,
        max_tries=2,
        giveup=lambda e: "OPERATION_TIMEOUT" not in str(e)
    )
    async def scrape_with_retries(self, config):
        self.logger.debug(f"[Chrono24] ⏳ Scraping URL: {config.url}")
        async with self.semaphore:
            return await self.client.async_scrape(config)

    async def search(self, query, category, product_id=None, image_query=None, search_query=None):
        base_url = (
            "https://www.chrono24.com/search/index.htm?"
            "catalogPageSize=60&sortorder=0&currencyId=USD&dosearch=true"
            "&query={query}&{filters}"
        )

        condition_filters = ["usedOrNew=used", "usedOrNew=new"]
        case_diameters = "&".join(f"caseDiameter={d}" for d in range(1, 38))

        urls = [
            base_url.format(query=quote(query), filters=f"{condition}&{case_diameters}")
            for condition in condition_filters
        ]

        self.logger.info(f"[Chrono24] 🔍 Launching Chrono24 search for: {query} with {len(urls)} conditions")
        tasks = [
            self.fetch_and_parse(url, category, product_id, image_query, query)
            for url in urls
        ]

        all_results = await asyncio.gather(*tasks)
        seen = set()
        merged = []
        for result_list in all_results:
            for item in result_list:
                if item["url"] not in seen:
                    seen.add(item["url"])
                    merged.append(item)

        self.logger.info(f"[Chrono24] ✅ Total unique Chrono24 items inserted: {len(merged)}")
        return merged

    async def fetch_and_parse(self, url, category, product_id, image_query, search_query):
        try:
            self.logger.debug(f"[Chrono24] 🌐 Scraping filtered URL: {url}")
            resp = await self.scrape_with_retries(ScrapeConfig(
                url=url,
                asp=True,
                render_js=False,
                country="US",
                session=Utility.generate_random_session(),
                proxy_pool="public_residential_pool",
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    )
                }
            ))
        except Exception as e:
            self.logger.exception(f"[Chrono24] ❌ Failed to scrape filtered URL: {e}")
            return []

        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)
        condition = query_params.get("usedOrNew", ["New"])[0]
        diameters = query_params.get("caseDiameter", [])
        size = f"{min(map(int, diameters))}mm - {max(map(int, diameters))}mm" if diameters else "Unknown"

        soup = BeautifulSoup(resp.scrape_result.get("content", ""), "html.parser")
        items_to_insert = []

        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(script.string)
                if isinstance(data, dict) and "@graph" in data:
                    for node in data["@graph"]:
                        if node.get("@type") == "AggregateOffer" and "offers" in node:
                            for offer in node["offers"]:
                                item_url = offer.get("url", "").strip()
                                if not item_url:
                                    continue

                                if is_duplicate_today(url=item_url):
                                    continue

                                title = offer.get("name", "").strip()
                                if not Utility.combined_similarity(title=title, query=search_query):
                                    continue
                                price_text = str(offer.get("price", "")).strip()
                                parsed_price = Price.fromstring(price_text)
                                price = parsed_price.amount_float

                                image_url = (
                                    offer.get("image", {}).get("contentUrl")
                                    if isinstance(offer.get("image"), dict)
                                    else offer.get("image")
                                )

                                location = Utility.extract_location_from_title(title=title)

                                if(condition.lower() == 'new'):
                                    condition = 'New'
                                elif(condition.lower() == 'used'):
                                    condition = 'Used'
                                else:
                                    condition = 'New'

                                base_data = {
                                    "product_id": product_id,
                                    "title": title,
                                    "listing_price": price,
                                    "url": item_url,
                                    "image_url": image_url,
                                    "condition": condition,
                                    "seller": offer.get("seller", {}).get("name", "Unknown"),
                                    "shipping": "Varies",
                                    "location": location,
                                    "top_rated": False,
                                    "source": "chrono24",
                                    "category": category,
                                    "image_query": image_query,
                                    "search_query": search_query,
                                    "size": size
                                }

                                product = ProductModel(base_data, category)
                                items_to_insert.append(product.to_dict())

            except json.JSONDecodeError:
                self.logger.warning("[Chrono24] ⚠️ Skipped invalid JSON-LD script block")

        if items_to_insert:
            self.bulk_insert_products(items_to_insert)

        self.logger.info(f"[Chrono24] 📦 Parsed {len(items_to_insert)} products from {url}")
        return items_to_insert

    def bulk_insert_products(self, items: list):
        if not items:
            return

        def write_batch(batch):
            try:
                with self.product_table.batch_writer(overwrite_by_pkeys=["url"]) as writer:
                    for item in batch:
                        writer.put_item(Item=item)
                        self.logger.info(f"Inserting... {item}")
                self.logger.info(f"[Chrono24] ✅ Inserted batch of {len(batch)} items. CHRONO")
            except Exception as e:
                self.logger.exception(f"[Chrono24] ❌ Batch insert failed: {e}")

        def chunks(data, size=25):
            for i in range(0, len(data), size):
                yield data[i:i + size]

        batches = list(chunks(items, 25))

        futures = [self.executor.submit(write_batch, batch) for batch in batches]

        for future in futures:
            future.result()
