import asyncio
import json
from urllib.parse import quote
from scrapfly import ScrapflyClient, ScrapeConfig, ScrapflyScrapeError
from utils.utility import Utility
from utils.logger import get_logger
from boto3.session import Session
from boto3.dynamodb.conditions import Attr
from models.product_item import ProductModel
from utils.antropic import AnthropicRefiner
from config.scrapper_config import Config
from price_parser import Price
import backoff


class VestiaireScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

        session = Session()
        dynamodb = session.resource("dynamodb", region_name="us-east-1")
        self.product_table = dynamodb.Table("ProductsTable")
        self.bags_table = dynamodb.Table("BagsTable")
        self.shoes_table = dynamodb.Table("ShoesTable")
        self.accessories_table = dynamodb.Table("AccessoriesTable")
        self.clothing_table = dynamodb.Table("ClothingTable")
        self.beauty_table = dynamodb.Table("BeautyTable")
        self.jewelry_table = dynamodb.Table("JewelryTable")

        self.anthropic_refiner = AnthropicRefiner()
        self.semaphore = asyncio.Semaphore(10)

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
        url = f"https://us.vestiairecollective.com/search/?q={quote(query)}"
        results = []

        try:
            self.logger.info(f"🔍 Searching Vestiaire for: {query}")
            first_page = await self.scrape_with_retries(ScrapeConfig(
                url=url,
                asp=True,
                render_js=True,
                rendering_wait=8000,
                wait_for_selector="script#__NEXT_DATA__",
                country="US",
                session=Utility.generate_random_session(),
                session_sticky_proxy=True,
                retry=False,
                timeout=120000
            ))

            xhr_calls = first_page.scrape_result["browser_data"]["xhr_call"]
            search_call = next((call for call in xhr_calls if "product/search" in call["url"]), None)
            if not search_call:
                self.logger.warning("❌ Couldn't find the product search API call.")
                return []

            initial_data = json.loads(search_call["response"]["body"])
            headers = search_call["headers"]
            payload = json.loads(search_call["body"])
            results_raw = initial_data.get("items", [])
            total_pages = min(initial_data.get("paginationStats", {}).get("totalPages", 1), 5)

            self.logger.info(f"✅ Page 1 scraped with {len(results_raw)} items")

            for page in range(1, total_pages):
                offset = page * 48
                payload["pagination"]["offset"] = offset

                page_data = await self.scrape_with_retries(ScrapeConfig(
                    url="https://search.vestiairecollective.com/v1/product/search",
                    method="POST",
                    headers={
                        **headers,
                        "Referer": url,
                        "Origin": "https://us.vestiairecollective.com",
                        "Content-Type": "application/json"
                    },
                    body=json.dumps(payload),
                    country="US",
                    session=Utility.generate_random_session(),
                    session_sticky_proxy=True,
                    asp=True,
                    timeout=120000,
                    retry=False
                ))

                page_items = json.loads(page_data.scrape_result["content"]).get("items", [])
                results_raw.extend(page_items)
                self.logger.info(f"✅ Page {page + 1} scraped with {len(page_items)} items")

            for item in results_raw:
                try:
                    product_url = f"https://www.vestiairecollective.com{item.get('link', '')}"
                    raw_price = item.get("price", {}).get("amount")
                    currency = item.get("price", {}).get("currency", "USD")
                    parsed_price = Price.fromstring(str(raw_price))
                    condition = self.anthropic_refiner.extract_condition_from_title(title=item.get('description', ''))
                    size = self.anthropic_refiner.extract_size_from_title(title=item.get('description', ''))

                    results.append({
                        "title": f"{item.get('brand', {}).get('name', '')} {item.get('title', '')}".strip(),
                        "listing_price": str(parsed_price.amount) if parsed_price.amount else str(raw_price),
                        "currency": parsed_price.currency or currency,
                        "url": product_url,
                        "image_url": item.get("images", [{}])[0].get("url"),
                        "condition": condition,
                        "seller": item.get('seller', '').get('firstname', ''),
                        "shipping": "Varies",
                        "location": item.get("seller", {}).get("location", {}).get("country", "Unknown"),
                        "top_rated": False,
                        "source": "vestiaire",
                        "product_id": product_id,
                        "category": category,
                        "image_query": image_query,
                        "search_query": search_query,
                        "size": size
                    })

                except Exception as e:
                    self.logger.warning(f"⚠️ Skipping item due to parse error: {e}")

            self.logger.info(f"🎯 Total detailed results from Vestiaire: {len(results)}")
            for item in results:
                self.insert_or_update_product(item, category)
            self.logger.info(f"✅ Vestiaire scraping completed: {len(results)} items found")
            return results

        except Exception as e:
            self.logger.exception(f"❌ Vestiaire scraping failed: {e}")
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