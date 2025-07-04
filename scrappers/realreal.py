import re
import uuid
import json
import asyncio
from urllib.parse import quote, urljoin
from scrapfly import ScrapflyClient, ScrapeConfig
from bs4 import BeautifulSoup
from utils.logger import get_logger
from utils.utility import Utility
from models.product_item import ProductModel
from models.shoes import ShoesModel
from models.bags import BagsModel
from models.accessories import AccessoriesModel
from models.clothing import ClothingModel
from boto3.session import Session
from boto3.dynamodb.conditions import Attr
from config.scrapper_config import Config

class RealRealScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

        session = Session()
        dynamodb = session.resource("dynamodb", region_name="us-east-2")
        self.product_table = dynamodb.Table("ProductsTable")
        self.shoes_table = dynamodb.Table("ShoesTable")
        self.bags_table = dynamodb.Table("BagsTable")
        self.accessories_table = dynamodb.Table("AccessoriesTable")
        self.clothing_table = dynamodb.Table("ClothingTable")

    async def search(self, query, category, product_id):
        self.logger.info(f"🔍 Searching The RealReal for query: {query}")
        url = f"https://www.therealreal.com/products?keywords={quote(query)}"

        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=url,
                render_js=True,
                asp=True,
                wait_for_selector=".product-card-wrapper",
                rendering_stage="complete",
                session=Utility.generate_random_session(),
                country="US",
                proxy_pool="public_residential_pool",
                timeout=120000,
                retry=False
            ))
        except Exception as e:
            self.logger.exception("❌ RealReal scraping failed")
            return []

        html = resp.scrape_result.get("content", "")
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(".product-card-wrapper")

        results = []
        detail_tasks = []

        for card in items:
            try:
                brand = card.select_one(".product-card__brand")
                name = card.select_one(".product-card__description")
                price = card.select_one(".product-card__price")
                image = card.select_one("img.product-card-image__img--one")
                link = name["href"] if name and name.has_attr("href") else None
                condition_tag = card.select_one(".product-card__item-condition")

                if not (brand and name and price and link):
                    continue

                full_url = f"https://www.therealreal.com{link}"

                base_data = {
                    "title": f"{brand.text.strip()} {name.text.strip()}",
                    "listing_price": re.sub(r"[^\d.]", "", price.text.strip()),
                    "url": full_url,
                    "image_url": image.get("data-src") if image and image.has_attr("data-src") else None,
                    "condition": condition_tag.text.strip() if condition_tag else "Unknown",
                    "seller": "TheRealReal Verified",
                    "shipping": "Varies (via The RealReal)",
                    "location": "US",
                    "top_rated": True,
                    "source": "therealreal"
                }

                results.append(base_data)
                detail_tasks.append(self.get_product_details(full_url))
            except Exception as e:
                self.logger.warning(f"⚠️ Skipping item due to parse error: {e}")
                detail_tasks.append(asyncio.sleep(0, result={}))

        self.logger.info(f"🧵 Fetching {len(detail_tasks)} product details concurrently...")
        detail_results = await asyncio.gather(*detail_tasks, return_exceptions=True)

        for item, detail in zip(results, detail_results):
            item["item_details"] = detail if isinstance(detail, dict) else {}
            item["product_id"] = product_id
            item["category"] = category
            self.insert_or_update_product(item, category)

        self.logger.info(f"✅ The RealReal Results: {len(results)} items found")
        return results

    async def get_product_details(self, product_url):
        self.logger.info(f"🔍 Fetching product details from The RealReal: {product_url}")
        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=product_url,
                render_js=True,
                asp=True,
                wait_for_selector="div.pdp-main-info",
                session=Utility.generate_random_session(),
                country="US",
                proxy_pool="public_residential_pool",
                timeout=120000,
                retry=False
            ))
        except Exception as e:
            self.logger.exception("❌ Failed to fetch product detail page")
            return {}

        try:
            html = resp.scrape_result.get("content", "")
            soup = BeautifulSoup(html, "html.parser")

            description_section = soup.select_one("#pdp-details-Description .pdp-description")
            description_items = [li.text.strip() for li in description_section.select("li")] if description_section else []

            details = {
                "description": description_items
            }

            details_section = soup.select("#pdp-details-Details .product-details-group")
            for group in details_section:
                for li in group.select("li"):
                    key_el = li.select_one("strong")
                    if key_el:
                        key = key_el.text.replace(":", "").strip().lower().replace(" ", "_")
                        value = key_el.next_sibling.strip() if key_el.next_sibling else ""
                        details[key] = value

            condition_active = soup.select_one(".condition__attribute--active .condition__text")
            condition_notes = soup.select_one(".condition__details dd")
            details["condition"] = condition_active.text.strip() if condition_active else ""
            details["condition_notes"] = condition_notes.text.strip() if condition_notes else ""

            return details

        except Exception as e:
            self.logger.exception("❌ Failed to parse product detail content")
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
            "Year": details.get("year", "Unknown"),
            "Size": details.get("size"),
            "Color": details.get("color"),
            "Box/Papers": details.get("original box"),
            "Dust Bag": details.get("dust bag") or details.get("description"),
            "Strap Drop": details.get("shoulder_strap_drop"),
            "Handle Drop": details.get("handle_drop"),
            "Height": details.get("height"),
            "Width": details.get("width"),
            "Depth": details.get("depth"),
            "Gender": details.get("gender"),
            "Item Code": details.get("item_#"),
            "Condition Notes": details.get("condition_notes"),
        }

        if category == "Shoes":
            meta = ShoesModel(product.product_id, specs)
            self.shoes_table.put_item(Item=meta.to_dict())
        elif category == "Bags":
            meta = BagsModel(product.product_id, specs)
            self.bags_table.put_item(Item=meta.to_dict())
        elif category == "Accessories":
            meta = AccessoriesModel(product.product_id, specs)
            self.accessories_table.put_item(Item=meta.to_dict())
        elif category == "Clothing":
            meta = ClothingModel(product.product_id, specs)
            self.clothing_table.put_item(Item=meta.to_dict())
        else:
            self.logger.warning(f"❌ Unknown category: {category}, skipping metadata.")
