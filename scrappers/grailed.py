import re
import uuid
import json
import asyncio
from urllib.parse import quote
from scrapfly import ScrapflyClient, ScrapeConfig
from bs4 import BeautifulSoup
from utils.utility import Utility
from utils.logger import get_logger
from models.product_item import ProductModel
from models.shoes import ShoesModel
from models.bags import BagsModel
from models.accessories import AccessoriesModel
from models.clothing import ClothingModel
from boto3.session import Session
from boto3.dynamodb.conditions import Attr
from config.scrapper_config import Config

class GrailedScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

        session = Session(profile_name=Config.AWS_USERNAME)
        dynamodb = session.resource("dynamodb", region_name="us-east-2")
        self.product_table = dynamodb.Table("ProductsTable")
        self.shoes_table = dynamodb.Table("ShoesTable")
        self.bags_table = dynamodb.Table("BagsTable")
        self.accessories_table = dynamodb.Table("AccessoriesTable")
        self.clothing_table = dynamodb.Table("ClothingTable")

    async def search(self, query, category, product_id):
        self.logger.info(f"🔍 Searching Grailed for query: {query}")
        url = f"https://www.grailed.com/shop?query={quote(query)}"

        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=url,
                asp=True,
                render_js=True,
                wait_for_selector="div.UserItem_root__8Q2R_",
                country="US",
                retry=False,
                timeout=120000
            ))
        except Exception as e:
            self.logger.exception(f"❌ Grailed scrape failed: {e}")
            return []

        html = resp.scrape_result.get("content", "")
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select("div.UserItem_root__8Q2R_")
        self.logger.info(f"🔍 Found {len(items)} items on Grailed")

        results = []
        detail_tasks = []

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
                price = re.sub(r"[^\d.]", "", price_el.text)
                original_price = re.sub(r"[^\d.]", "", orig_price_el.text) if orig_price_el else None
                percent_off = discount_el.text.strip() if discount_el else None
                image_url = "https:" + image_el["src"] if image_el and image_el.has_attr("src") else None
                full_url = f"https://www.grailed.com{href}"

                base_data = {
                    "title": f"{designer} {title}".strip(),
                    "listing_price": price,
                    "original_price": original_price,
                    "discount": percent_off,
                    "url": full_url,
                    "image_url": image_url,
                    "condition": "Used",
                    "seller": "Grailed Seller",
                    "shipping": "Varies",
                    "location": "US",
                    "top_rated": False,
                    "source": "grailed"
                }
                results.append(base_data)
                detail_tasks.append(self.get_product_details(full_url))
            except Exception as e:
                self.logger.warning(f"⚠️ Skipping item due to parse error: {e}")
                detail_tasks.append(asyncio.sleep(0, result={}))

        self.logger.info(f"🧵 Fetching {len(detail_tasks)} item details concurrently...")
        detail_results = await asyncio.gather(*detail_tasks, return_exceptions=True)

        for item, detail in zip(results, detail_results):
            item["item_details"] = detail if isinstance(detail, dict) else {}
            item["product_id"] = product_id
            item["category"] = category
            self.insert_or_update_product(item, category)

        self.logger.info(f"✅ Grailed results fetched: {len(results)}")
        return results

    async def get_product_details(self, product_url):
        self.logger.info(f"🔍 Fetching Grailed item details: {product_url}")
        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=product_url,
                asp=True,
                render_js=True,
                wait_for_selector="div.MainContent_item__dpT9l",
                timeout=120000,
                retry=False,
                country="US",
                session=Utility.generate_random_session()
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch item detail page: {e}")
            return {}

        try:
            html = resp.scrape_result.get("content", "")
            soup = BeautifulSoup(html, "html.parser")
            container = soup.select_one("div.MainContent_item__dpT9l.MainContent_details__Co188")
            if not container:
                self.logger.warning("⚠️ Product details container not found")
                return {}

            designer_tags = container.select("p.Details_designers__AG6hs a.Designers_designer__Mbyk_")
            designers = " × ".join([d.text.strip() for d in designer_tags]) if designer_tags else ""

            title_tag = container.select_one("h1.Details_title__8rdLK")
            title = title_tag.text.strip() if title_tag else ""

            size_tag = container.select_one("p[data-testid='non-mobile-size'] span.Details_value__XcEpo")
            size = size_tag.text.strip() if size_tag else ""

            color_tag = container.select_one("span.Color_value__TrEnh")
            color = color_tag.text.strip() if color_tag else ""

            condition_tag = container.select_one("p:-soup-contains('Condition') span.Details_value__XcEpo")
            if not condition_tag:
                condition_tag = container.select_one("p.Details_detail__7xjgu span.Details_value__XcEpo")
            condition = condition_tag.text.strip() if condition_tag else ""

            details = {
                "designers": designers,
                "title": title,
                "size": size,
                "color": color,
                "condition": condition,
                "source": "grailed"
            }
            return details

        except Exception as e:
            self.logger.exception(f"❌ Failed to parse item details: {e}")
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
            "Brand": details.get("designers"),
            "Condition": details.get("condition", base_data.get("condition")),
            "Year": details.get("year", "Unknown"),
            "Size": details.get("size"),
            "Color": details.get("color"),
            "Box/Papers": details.get("box"),
            "Dust Bag": details.get("dust bag") or details.get("description")
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