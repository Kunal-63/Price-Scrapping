import re
import uuid
import json
import asyncio
from urllib.parse import quote
from scrapfly import ScrapflyClient, ScrapeConfig, ScrapflyScrapeError
from bs4 import BeautifulSoup
from utils.utility import Utility
from utils.logger import get_logger
from models.product_item import ProductModel
from price_parser.parser import Price
from boto3.session import Session
from boto3.dynamodb.conditions import Attr
from config.scrapper_config import Config
from utils.antropic import AnthropicRefiner
import backoff
from concurrent.futures import ThreadPoolExecutor

class FarfetchScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

        session = Session()
        dynamodb = session.resource("dynamodb", region_name="us-east-1")
        self.product_table = dynamodb.Table("ProductsTable")

        self.semaphore = asyncio.Semaphore(10)
        self.anthropic_refiner = AnthropicRefiner()
        self.executor = ThreadPoolExecutor(max_workers=8)


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
        self.logger.info(f"🔍 Searching Farfetch for query: {query}")
        url = f"https://www.farfetch.com/shopping/men/items.aspx?query={quote(query)}"

        try:
            resp = await self.scrape_with_retries(ScrapeConfig(
                url=url,
                asp=True,
                render_js=True,
                wait_for_selector="article[data-testid='product-card']",
                country="US",
                session=Utility.generate_random_session(),
                retry=False,
                timeout=120000
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch Farfetch listings: {e}")
            return []

        html = resp.scrape_result.get("content", "")
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select("article[data-testid='product-card']")

        results = []

        for item in items:
            try:
                brand = item.select_one("p[data-component='ProductCardBrandName']")
                title = item.select_one("p[data-component='ProductCardDescription']")
                price_el = item.select_one("p[data-component='PriceFinal']")
                link_tag = item.select_one("a[data-component='ProductCardLink']")
                image_tag = item.select_one("link[itemprop='image']")

                if not (brand and title and price_el and link_tag):
                    continue

                # Extract price
                price_text = price_el.text.strip()
                price_data = Price.fromstring(price_text)
                listing_price = price_data.amount_float
                currency = price_data.currency or "USD"

                # Build product data
                product_url = f"https://www.farfetch.com{link_tag['href']}"
                image_url = image_tag["href"] if image_tag else None
                combined_title = f"{brand.text.strip()} {title.text.strip()}"

                # Try to extract size from title
                size_match = re.search(r'\b(?:Size|EU|US)?\s*([A-Za-z0-9./]+)\b', combined_title)
                size = size_match.group(1).strip() if size_match else None
                size = Utility.refine_us_size_string(size)

                if not size:
                    size = self.anthropic_refiner.extract_size_from_title(title=title)

                condition = self.anthropic_refiner.extract_condition_from_title(title=title)

                if('new' in condition.lower()):
                    condition = "New"
                elif('pre' in condition.lower()):
                    condition = "Excellent"
                else:
                    condition = "Varies"

                location = Utility.extract_location_from_title(title=combined_title)

                result = {
                    "product_id": product_id,
                    "title": combined_title,
                    "listing_price": listing_price,
                    "original_price": None,
                    "discount": None,
                    "currency": 'USD' if currency == "$" else currency,
                    "url": product_url,
                    "image_url": image_url,
                    "condition": condition,
                    "seller": "Farfetch",
                    "shipping": "Varies by region",
                    "location": location,
                    "top_rated": True,
                    "source": "farfetch",
                    "image_query": image_query,
                    "search_query": search_query,
                    "size": size,
                    "category": category,
                    "bid_count": "0"
                }

                results.append(result)

            except Exception as e:
                self.logger.warning(f"⚠️ Skipping item due to parse error: {e}")

        products_to_insert = [ProductModel(item, category).to_dict() for item in results]
        self.bulk_insert_products(products_to_insert)

        self.logger.info(f"✅ Farfetch results fetched: {len(results)}")
        return results

    def bulk_insert_products(self, items: list):
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

        # Split into chunks of 25
        def chunks(data, size=25):
            for i in range(0, len(data), size):
                yield data[i:i + size]

        batches = list(chunks(items, 25))
        futures = [self.executor.submit(write_batch, batch) for batch in batches]

        # Optional: wait for all threads to finish
        for f in futures:
            f.result()

