import json
from urllib.parse import quote
from scrapfly import ScrapflyClient, ScrapeConfig, ScrapflyScrapeError
from bs4 import BeautifulSoup
from utils.logger import get_logger
import asyncio
from utils.utility import Utility
from datetime import datetime
import uuid
import re
import backoff
from boto3.session import Session
from models.product_item import ProductModel
from models.shoes import ShoesModel
from models.bags import BagsModel
from models.accessories import AccessoriesModel
from models.clothing import ClothingModel
from config.scrapper_config import Config

class Chrono24Scraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

        session = Session()
        dynamodb = session.resource("dynamodb", region_name="us-east-1")
        self.product_table = dynamodb.Table("ProductsTable")
        self.shoes_table = dynamodb.Table("ShoesTable")
        self.bags_table = dynamodb.Table("BagsTable")
        self.accessories_table = dynamodb.Table("AccessoriesTable")
        self.clothing_table = dynamodb.Table("ClothingTable")
        self.beauty_table = dynamodb.Table("BeautyTable")
        self.jewelry_table = dynamodb.Table("JewelryTable")

        self.semaphore = asyncio.Semaphore(5) 

    @backoff.on_exception(
        backoff.expo,
        ScrapflyScrapeError,
        max_tries=4,
        giveup=lambda e: "OPERATION_TIMEOUT" not in str(e)  # retry only if it's timeout
    )
    async def scrape_with_retries(self, config):
        self.logger.debug(f"⏳ Scraping URL: {config.url}")
        async with self.semaphore:
            return await self.client.async_scrape(config)

    async def search(self, query, category, product_id, image_query, search_query):
        url = f"https://www.chrono24.com/search/index.htm?dosearch=true&query={quote(query)}"
        self.logger.info(f"📡 Fetching Chrono24 results for query: {query}")

        try:
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
            self.logger.exception(f"❌ Chrono24 scraping failed: {e}")
            return []

        html = resp.scrape_result.get("content", "")
        soup = BeautifulSoup(html, "html.parser")

        results, detail_tasks = [], []

        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(script.string)
                if isinstance(data, dict) and "@graph" in data:
                    for node in data["@graph"]:
                        if node.get("@type") == "AggregateOffer" and "offers" in node:
                            for offer in node["offers"]:
                                url = offer.get("url", "").strip()
                                if not url:
                                    continue

                                base_data = {
                                    "title": offer.get("name", "").strip(),
                                    "product_id": product_id,
                                    "listing_price": re.sub(r"[^\d.]", "", str(offer.get("price", ""))),
                                    "url": url,
                                    "image_url": offer.get("image", {}).get("contentUrl")
                                        if isinstance(offer.get("image"), dict)
                                        else offer.get("image", None),
                                    "condition": offer.get("itemCondition", "Used"),
                                    "seller": offer.get("seller", {}).get("name", "Unknown"),
                                    "shipping": "Varies",
                                    "location": "Unknown",
                                    "top_rated": False,
                                    "source": "chrono24",
                                    "category": category,
                                    "image_query": image_query,
                                    "search_query": search_query
                                }

                                results.append(base_data)
                                detail_tasks.append(self.get_product_detail(url))

            except json.JSONDecodeError:
                self.logger.warning("⚠️ Skipped invalid JSON script tag")

        self.logger.info(f"🧵 Fetching details for {len(detail_tasks)} items concurrently...")
        details_list = await asyncio.gather(*detail_tasks, return_exceptions=True)

        for base_data, detail in zip(results, details_list):
            if isinstance(detail, dict):
                base_data["item_specifics"] = detail
                self.insert_or_update_product(base_data, category)

        self.logger.info(f"✅ Chrono24 items inserted: {len(results)}")
        return results
    
    async def get_product_detail(self, url):
        self.logger.info(f"🔍 Fetching product detail from: {url}")
        try:
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
            self.logger.exception(f"❌ Failed to fetch product detail: {e}")
            return {}

        html = resp.scrape_result.get("content", "")
        return self.extract_item_details(html)

    def extract_item_details(self, html: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        item_details = {}
        table = soup.find("table")
        if not table:
            return item_details

        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) == 2:
                key = tds[0].get_text(strip=True).replace(":", "")
                value = tds[1].get_text(" ", strip=True)
                item_details[key] = value

        return item_details

    def insert_or_update_product(self, base_data: dict, category: str):
        from boto3.dynamodb.conditions import Attr

        existing = self.product_table.scan(
            FilterExpression=Attr("url").eq(base_data["url"])
        )

        product = ProductModel(base_data, category)

        if existing["Items"]:
            self.logger.info(f"🔄 Updating existing product in DB: {base_data['url']}")
        else:
            self.logger.info(f"➕ Inserting new product in DB: {base_data['url']}")

        self.product_table.put_item(Item=product.to_dict())

        specs = base_data.get("item_specifics", {})
        if not specs:
            self.logger.warning(f"⚠️ No item_specifics found for {base_data['url']}, skipping metadata insert.")
            return

        def clean_size(val):
            return re.sub(r"[^\d.\smm]", "", val).strip() if val else None

        if category == "Shoes":
            meta = ShoesModel(product.product_id, {
                "Brand": specs.get("Brand"),
                "Condition": specs.get("Condition"),
                "Year": specs.get("Year of production"),
                "Colorway": specs.get("Dial") or specs.get("Bezel material"),
                "Size": clean_size(specs.get("Case diameter")),
                "Box/Papers": specs.get("Scope of delivery")
            },base_data["url"])
            self.shoes_table.put_item(Item=meta.to_dict())

        elif category == "Bags":
            meta = BagsModel(product.product_id, {
                "Brand": specs.get("Brand"),
                "Condition": specs.get("Condition"),
                "Year": specs.get("Year of production"),
                "Size": clean_size(specs.get("Case diameter")),
                "Color": specs.get("Dial") or specs.get("Bracelet color") or specs.get("Bezel material"),
                "Box/Papers": specs.get("Scope of delivery"),
                "Dust Bag": None
            }, base_data["url"])
            self.bags_table.put_item(Item=meta.to_dict())

        elif category == "Accessories":
            meta = AccessoriesModel(product.product_id, {
                "Brand": specs.get("Brand"),
                "Condition": specs.get("Condition"),
                "Year": specs.get("Year of production")
            }, base_data["url"])
            self.accessories_table.put_item(Item=meta.to_dict())

        elif category == "Clothing":
            meta = ClothingModel(product.product_id, {
                "Brand": specs.get("Brand"),
                "Condition": specs.get("Condition"),
                "Size": clean_size(specs.get("Case diameter"))
            }, base_data["url"])
            self.clothing_table.put_item(Item=meta.to_dict())
        elif category == "Beauty":
            from models.beauty import BeautyModel
            meta = BeautyModel(product.product_id, {
                "Brand": specs.get("Brand"),
                "Condition": specs.get("Condition"),
                "Size": clean_size(specs.get("Case diameter")),
                "Type": specs.get("Dial") or specs.get("Bezel material")
            }, base_data["url"])
            self.beauty_table.put_item(Item=meta.to_dict())
        elif category == "Jewelry":
            from models.jewelry import JewelryModel
            meta = JewelryModel(product.product_id, {
                "Brand": specs.get("Brand"),
                "Condition": specs.get("Condition"),
                "Metal": specs.get("Metal"),
                "Stone": specs.get("Stone"),
                "Box": specs.get("Scope of delivery")
            }, base_data["url"])
            self.jewelry_table.put_item(Item=meta.to_dict())
        else:
            self.logger.warning(f"❌ Unknown category: {category}, skipping metadata.")
        self.logger.info(f"✅ Product metadata inserted for {base_data['url']} in category {category}")