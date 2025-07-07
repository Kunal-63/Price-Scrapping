import asyncio
import json
from scrapfly import ScrapflyClient, ScrapeConfig, ScrapflyScrapeError
from bs4 import BeautifulSoup
from utils.utility import Utility
from utils.logger import get_logger
from boto3.session import Session
from boto3.dynamodb.conditions import Attr
from models.product_item import ProductModel
from models.bags import BagsModel
from models.shoes import ShoesModel
from models.accessories import AccessoriesModel
from models.clothing import ClothingModel
from config.scrapper_config import Config
from models.beauty import BeautyModel
from models.jewelry import JewelryModel
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

    async def search(self, query, category, product_id, image_query):
        url = f"https://us.vestiairecollective.com/search/?q={query.replace(' ', '%20')}"
        session_id = Utility.generate_random_session()
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

            # Additional pages
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

            # Detailed results with per-item fetching
            for item in results_raw:
                try:
                    product_url = f"https://www.vestiairecollective.com{item.get('link', '')}"
                    details = await self.fetch_product_details(product_url, session_id)

                    results.append({
                        "title": f"{item.get('brand', {}).get('name', '')} {item.get('title', '')}".strip(),
                        "listing_price": str(item.get("price", {}).get("amount")),
                        "currency": item.get("price", {}).get("currency", "USD"),
                        "url": product_url,
                        "image_url": item.get("images", [{}])[0].get("url"),
                        "condition": item.get("condition", {}).get("label") or "Unknown",
                        "seller": "Vestiaire Seller",
                        "shipping": "Varies",
                        "location": item.get("seller", {}).get("location", {}).get("country", "Unknown"),
                        "top_rated": False,
                        "source": "vestiaire",
                        "product_id": product_id,
                        "category": category,
                        "item_details": details,
                        "image_query": image_query
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

    async def fetch_product_details(self, url, session_id):
        self.logger.info(f"🔍 Fetching product details from: {url}")
        try:
            resp = await self.scrape_with_retries(ScrapeConfig(
                url=url,
                asp=True,
                render_js=True,
                rendering_wait=9000,
                wait_for_selector="div.product-description_description__container__core__s6AGP",
                country="US",
                session=Utility.generate_random_session(),
                session_sticky_proxy=True,
                retry=False,
                timeout=120000,
                auto_scroll=True
            ))

            html = resp.scrape_result["content"]
            soup = BeautifulSoup(html, "html.parser")
            details = {}

            # Description
            desc = soup.select_one("div.product-seller-description_sellerDescription__Lh7HQ p")
            if desc:
                details["description"] = desc.get_text(strip=True)

            # General Info
            for li in soup.select("ul.product-description-list_descriptionList__list__L_0k1 li"):
                try:
                    k = li.select_one("span.product-description-list_descriptionList__property__SKPsZ")
                    v = li.select_one("span.product-description-list_descriptionList__value__rtnBQ")
                    if k and v:
                        key = k.text.strip().replace(":", "").lower().replace(" ", "_")
                        val = v.text.strip().replace("\n", ", ")
                        details[key] = val
                except Exception as e:
                    self.logger.warning(f"⚠️ Skipping general info due to error: {e}")

            # Measurements
            for li in soup.select("div.product-description-list_descriptionList__column--right__SNBJj li"):
                try:
                    k = li.select_one("span.product-description-list_descriptionList__property__SKPsZ")
                    v = li.select_one("span.product-description-list_descriptionList__value__rtnBQ")
                    if k and v:
                        key = k.text.strip().replace(":", "").lower().replace(" ", "_")
                        val = v.text.strip()
                        details[key] = val
                except Exception as e:
                    self.logger.warning(f"⚠️ Skipping measurement due to error: {e}")

            return details

        except Exception as e:
            self.logger.warning(f"⚠️ Failed to fetch product details from {url}: {e}")
            return {}

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

        details = base_data.get("item_details", {})
        specs = {
            "Brand": details.get("brand") or details.get("designer"),
            "Condition": base_data.get("condition", "Unknown"),
            "Size": details.get("size"),
            "Color": details.get("color"),
            "Box/Papers": details.get("material"),
            "Dust Bag": details.get("dust bag") or details.get("description"),
            "Year": details.get("year", "Unknown"),
            "Designer": details.get("designers") or details.get("brand"),
            "Title": details.get("title"),
            "Stone": details.get("stone"),
            "Metal": details.get("metal"),
            "Box": details.get("box"),
            "Gender": details.get("gender"),
        }

        if category == "Shoes":
            meta = ShoesModel(product.product_id, specs, base_data["url"])
            self.shoes_table.put_item(Item=meta.to_dict())
        elif category == "Bags":
            meta = BagsModel(product.product_id, specs, base_data["url"])
            self.bags_table.put_item(Item=meta.to_dict())
        elif category == "Accessories":
            meta = AccessoriesModel(product.product_id, specs, base_data["url"])
            self.accessories_table.put_item(Item=meta.to_dict())
        elif category == "Clothing":
            meta = ClothingModel(product.product_id, specs, base_data["url"])
            self.clothing_table.put_item(Item=meta.to_dict())
        elif category == "Beauty":
            meta = BeautyModel(product.product_id, specs, base_data["url"])
            self.beauty_table.put_item(Item=meta.to_dict())
        elif category == "Jewelry":
            meta = JewelryModel(product.product_id, specs, base_data["url"])
            self.jewelry_table.put_item(Item=meta.to_dict())
        else:
            self.logger.warning(f"❌ Unknown category: {category}, skipping metadata.")
