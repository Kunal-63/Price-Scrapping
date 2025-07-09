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

class FarfetchScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

        session = Session()
        dynamodb = session.resource("dynamodb", region_name="us-east-1")
        self.product_table = dynamodb.Table("ProductsTable")

        self.semaphore = asyncio.Semaphore(10)
        self.anthropic_refiner = AnthropicRefiner()

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

                if not size:
                    size = self.anthropic_refiner.extract_size_from_title(title=title)

                condition = self.anthropic_refiner.extract_condition_from_title(title=title)

                location = self.anthropic_refiner.extract_location_from_title(title=title)

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

        for item in results:
            self.insert_or_update_product(item, category)

        self.logger.info(f"✅ Farfetch results fetched: {len(results)}")
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
