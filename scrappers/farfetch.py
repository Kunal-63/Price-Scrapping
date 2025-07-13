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
from utils.database import is_duplicate_today

class FarfetchScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

        session = Session()
        dynamodb = session.resource("dynamodb", region_name="us-east-1")
        self.product_table = dynamodb.Table("ProductsTable")

        self.semaphore = asyncio.Semaphore(10)
        self.anthropic_refiner = AnthropicRefiner()
        self.executor = ThreadPoolExecutor(max_workers=20)


    @backoff.on_exception(
        backoff.expo,
        ScrapflyScrapeError,
        max_tries=2,
        giveup=lambda e: "OPERATION_TIMEOUT" not in str(e)
    )
    async def scrape_with_retries(self, config):
        self.logger.debug(f"[Farfetch] ⏳ Scraping URL: {config.url}")
        async with self.semaphore:
            return await self.client.async_scrape(config)

    async def search(self, query, category, product_id, image_query, search_query):
        self.logger.info(f"[Farfetch] 🔍 Searching Farfetch for query: {query}")
        url = f"https://www.farfetch.com/shopping/men/search/items.aspx?q={quote(query)}&ffref=Search&qr=3"


        try:
            resp = await self.scrape_with_retries(ScrapeConfig(
                url=url,
                country="US",
                session=Utility.generate_random_session(),
                retry=False,
                timeout=15000,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache',
                }
            ))
        except Exception as e:
            self.logger.exception(f"[Farfetch] ❌ Failed to fetch Farfetch listings: {e}")
            return []

        html = resp.scrape_result.get("content", "")
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select("li[data-testid='productCard']")

        results = []

        for item in items:
            try:
                link_tag = item.select_one("a[data-component='ProductCardLink']")
                brand = item.select_one("p[data-component='ProductCardBrandName']")
                title_tag = item.select_one("p[data-component='ProductCardDescription']")
                price_el = item.select_one("p[data-component='PriceFinal']")
                condition_tag = item.select_one("p[data-component='LabelPrimary']")
                image_tag = item.select_one("img[data-component='ProductCardImagePrimary']")

                if not (link_tag and brand and title_tag and price_el and image_tag):
                    continue

                product_url = f"https://www.farfetch.com{link_tag['href']}"
                # if is_duplicate_today(url=product_url):
                #     continue

                title_text = f"{brand.text.strip()} {title_tag.text.strip()}"
                if not Utility.combined_similarity(title=title_text, query=query, threshold=0.84):
                    continue

                price_data = Price.fromstring(price_el.text.strip())
                listing_price = price_data.amount_float
                currency = price_data.currency or "USD"

                image_url = image_tag["src"]

                size = Utility.extract_size_from_title(title=title_text)
                size = Utility.refine_us_size_string(size)

                condition = condition_tag.text.strip() if condition_tag else "New"
                if 'vintage' in condition.lower() or 'pre' in condition.lower():
                    condition = "Excellent"
                elif 'new' in condition.lower():
                    condition = "New"
                else:
                    condition = "New"

                location = Utility.extract_location_from_title(title=title_text)

                result = {
                    "product_id": product_id,
                    "title": title_text,
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
                self.logger.warning(f"[Farfetch] ⚠️ Skipping item due to parse error: {e}")

        products_to_insert = [ProductModel(item, category).to_dict() for item in results]
        self.bulk_insert_products(products_to_insert)

        self.logger.info(f"[Farfetch] ✅ Farfetch results fetched: {len(results)}")
        return results

    def bulk_insert_products(self, items: list):
        if not items:
            return

        def write_batch(batch):
            try:
                with self.product_table.batch_writer(overwrite_by_pkeys=["url"]) as writer:
                    for item in batch:
                        writer.put_item(Item=item)
                self.logger.info(f"[Farfetch] ✅ Inserted batch of {len(batch)} items. FARFETCH")
            except Exception as e:
                self.logger.exception(f"[Farfetch] ❌ Batch insert failed: {e}")

        def chunks(data, size=25):
            for i in range(0, len(data), size):
                yield data[i:i + size]

        batches = list(chunks(items, 25))
        futures = [self.executor.submit(write_batch, batch) for batch in batches]

        for f in futures:
            f.result()

