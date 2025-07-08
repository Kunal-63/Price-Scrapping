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
from utils.gemini import GeminiRefiner
import backoff

class GrailedScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()
        session = Session()
        dynamodb = session.resource("dynamodb", region_name="us-east-1")
        self.product_table = dynamodb.Table("ProductsTable")
        self.semaphore = asyncio.Semaphore(10)
        self.gemini_refiner = GeminiRefiner()

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
        self.logger.info(f"🔍 Searching Grailed for query: {query}")
        url = f"https://www.grailed.com/shop?query={quote(query)}"
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
            self.logger.error(f"❌ Grailed scrape failed: {e}")
            return []

        html = resp.scrape_result.get("content", "")
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select("div.UserItem_root__8Q2R_")
        self.logger.info(f"🔍 Found {len(items)} items on Grailed")

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

                # Attempt to extract size (fallback to Gemini if needed)
                size_match = re.search(r"Size\s*([A-Za-z0-9./]+)", title, re.IGNORECASE)
                size = size_match.group(1).strip() if size_match else None

                if not size:
                    size = self.gemini_refiner.extract_size_from_title(title=title)

                condition = self.gemini_refiner.extract_condition_from_title(title=title)

                results.append({
                    "title": combined_title,
                    "listing_price": listing_price,
                    "original_price": original_price,
                    "discount": percent_off,
                    "currency": 'USD' if currency == '$' else currency,
                    "url": full_url,
                    "image_url": image_url,
                    "condition": condition,
                    "seller": "Grailed Seller",
                    "shipping": "Varies",
                    "location": "US",
                    "top_rated": False,
                    "source": "grailed",
                    "product_id": product_id,
                    "category": category,
                    "image_query": image_query,
                    "search_query": search_query,
                    "size": size,
                    "bid_count": "0"
                })
            except Exception as e:
                self.logger.warning(f"⚠️ Error parsing item: {e}")

        for item in results:
            self.insert_or_update_product(item, category)

        self.logger.info(f"✅ Grailed scraping completed: {len(results)} items found")
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
