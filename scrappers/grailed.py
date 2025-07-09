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
from boto3.session import Session
from boto3.dynamodb.conditions import Attr
from config.scrapper_config import Config
from price_parser.parser import Price
from utils.antropic import AnthropicRefiner
import backoff
from concurrent.futures import ThreadPoolExecutor

class GrailedScraper:
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
        async with self.semaphore:
            return await self.client.async_scrape(config)
        
    async def search(self, query, category, product_id, image_query, search_query):
        self.logger.info(f"🔍 Searching Grailed for query: {query} across all conditions")

        # Define all condition filters
        condition_filters = {
            "is_new": "New",
            "is_gently_used": "Gently Used",
            "is_used": "Used",
            "is_worn": "Worn",
            "is_not_specified": "Not Specified"
        }

        # Launch concurrent tasks for each condition
        tasks = [
            self.search_by_condition(query, category, product_id, image_query, search_query, condition_code, condition_label)
            for condition_code, condition_label in condition_filters.items()
        ]

        results = await asyncio.gather(*tasks)
        flat_results = [item for sublist in results for item in sublist]
        return flat_results

    async def search_by_condition(self, query, category, product_id, image_query, search_query, condition_code, condition_label):
        url = f"https://www.grailed.com/shop?query={quote(query)}&condition={quote(condition_code)}"
        self.logger.info(f"🔍 Scraping: {condition_label} items for {query}")

        try:
            resp = await self.scrape_with_retries(ScrapeConfig(
                url=url,
                asp=True,
                render_js=True,
                wait_for_selector="div.UserItem_root__8Q2R_",
                country="US",
                session=Utility.generate_random_session(),
                retry=False,
                timeout=120000
            ))
        except Exception as e:
            self.logger.error(f"❌ Failed to scrape {condition_label}: {e}")
            return []

        html = resp.scrape_result.get("content", "")
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select("div.UserItem_root__8Q2R_")
        self.logger.info(f"✅ {len(items)} {condition_label} items found")

        results = []
        for item in items:
            try:
                anchor = item.select_one("a.UserItem_link__kgEWg")
                if not anchor:
                    continue

                title_el = anchor.select_one("p.UserItem_title__riOTf")
                designer_el = anchor.select_one("p.UserItem_designer__N8CxZ")
                price_el = item.select_one("span[data-testid='Current']")
                orig_price_el = item.select_one("span[data-testid='Original']")
                discount_el = item.select_one("span[data-testid='PercentOff']")
                image_el = anchor.select_one("img")
                href = anchor.get("href")

                if not (title_el and price_el and href):
                    continue

                title = title_el.text.strip()
                designer = designer_el.text.strip() if designer_el else ""
                combined_title = f"{designer} {title}".strip()

                price_text = price_el.text.strip()
                price_data = Price.fromstring(price_text)
                listing_price = price_data.amount_float
                currency = price_data.currency or "USD"

                original_price = float(re.sub(r"[^\d.]", "", orig_price_el.text)) if orig_price_el else None
                percent_off = discount_el.text.strip() if discount_el else None
                image_url = "https:" + image_el["src"] if image_el and image_el.has_attr("src") else ""
                full_url = f"https://www.grailed.com{href}"

                # Attempt to extract size (fallback to Gemini/Anthropic if needed)
                size_match = re.search(r"Size\s*([A-Za-z0-9./]+)", title, re.IGNORECASE)
                size = size_match.group(1).strip() if size_match else self.anthropic_refiner.extract_size_from_title(combined_title)
                size = Utility.refine_us_size_string(size)

                location = Utility.extract_location_from_title(title=combined_title)

                if(condition_code == "is_gently_used"):
                    condition_label = "Like New"
                elif(condition_code == "is_used"):
                    condition_label = "Good"
                elif(condition_code == "is_worn"):
                    condition_label = "Fair"
                elif(condition_code == "is_not_specified"):
                    condition_label = "Good"

                result = {
                    "title": combined_title,
                    "listing_price": listing_price,
                    "original_price": original_price,
                    "discount": percent_off,
                    "currency": 'USD' if currency == '$' else currency,
                    "url": full_url,
                    "image_url": image_url,
                    "condition": condition_label,
                    "seller": "Grailed Seller",
                    "shipping": "Varies",
                    "location": location,
                    "top_rated": False,
                    "source": "grailed",
                    "product_id": product_id,
                    "category": category,
                    "image_query": image_query,
                    "search_query": search_query,
                    "size": size,
                    "bid_count": "0"
                }

                product = ProductModel(result, category)
                results.append(product.to_dict())
            except Exception as e:
                self.logger.warning(f"⚠️ Error parsing item: {e}")
        
        self.bulk_insert_products(results)

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

        def chunks(data, size=25):
            for i in range(0, len(data), size):
                yield data[i:i + size]

        batches = list(chunks(items, 25))
        futures = [self.executor.submit(write_batch, batch) for batch in batches]

        for future in futures:
            future.result()
