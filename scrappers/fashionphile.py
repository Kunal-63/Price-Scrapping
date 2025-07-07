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
import datetime
from config.scrapper_config import Config
from models.beauty import BeautyModel
from models.jewelry import JewelryModel


class FashionphileScraper:
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
        self.beauty_table = dynamodb.Table("BeautyTable")
        self.jewelry_table = dynamodb.Table("JewelryTable")

    async def search(self, query, category, product_id):
        self.logger.info(f"🔍 Searching Fashionphile for query: {query}")
        url = f"https://www.fashionphile.com/shop?search={quote(query)}"
        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=url,
                render_js=True,
                asp=True,
                wait_for_selector="li.ais-Hits-item",
                session=Utility.generate_random_session(),
                proxy_pool="public_residential_pool",
                retry=False,
                timeout=120000,
                country="US"
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch Fashionphile listings: {e}")
            return []

        html = resp.scrape_result["content"]
        soup = BeautifulSoup(html, "html.parser")
        items = soup.find_all("li", class_="ais-Hits-item")

        results = []
        detail_tasks = []

        for card in items:
            try:
                link_tag = card.find("a", class_="linkHover")
                title_tag = card.find("a", class_="hitTitle")
                brand_tag = card.find("span", class_="brand")
                price_tag = card.find("span", itemprop="price")
                image_tag = link_tag.find("img") if link_tag else None
                condition_tag = card.find("div", class_="condition-container")

                if not (link_tag and title_tag and brand_tag and price_tag):
                    continue

                title = f"{brand_tag.text.strip()} {title_tag.text.strip()}"
                price = re.sub(r"[^\d.]", "", price_tag.text.strip())
                url_full = urljoin("https://www.fashionphile.com", link_tag["href"])
                image_url = image_tag["src"] if image_tag and image_tag.has_attr("src") else None
                condition = condition_tag.text.strip() if condition_tag else "Unknown"

                base_data = {
                    "title": title,
                    "listing_price": price,
                    "url": url_full,
                    "image_url": image_url,
                    "condition": condition,
                    "seller": "Fashionphile Verified",
                    "shipping": "Free Shipping in US",
                    "location": "US",
                    "top_rated": True,
                    "source": "fashionphile"
                }
                results.append(base_data)
                detail_tasks.append(self.get_product_details(url_full))

            except Exception as e:
                self.logger.warning(f"⚠️ Failed to parse a product card: {e}")
                detail_tasks.append(asyncio.sleep(0, result={}))

        self.logger.info(f"🧵 Fetching item details concurrently for {len(detail_tasks)} products...")
        detail_results = await asyncio.gather(*detail_tasks, return_exceptions=True)

        for item, detail in zip(results, detail_results):
            item["item_details"] = detail if isinstance(detail, dict) else {}
            item["product_id"] = product_id
            item["category"] = category
            self.insert_or_update_product(item, category)

        self.logger.info(f"✅ Fashionphile results fetched: {len(results)}")
        return results
    
    async def get_product_details(self, product_url):
        self.logger.info(f"🔍 Fetching Fashionphile item details: {product_url}")
        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=product_url,
                render_js=True,
                asp=True,
                wait_for_selector="div.accordionPanel",
                js="""
                    setTimeout(() => {
                        document.querySelectorAll('button.pmButton[aria-label="Open"]')
                          .forEach(btn => btn.click());
                    }, 1000);
                """,
                session=Utility.generate_random_session(),
                proxy_pool="public_residential_pool",
                country="US",
                timeout=120000,
                retry=False
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch item detail page: {e}")
            return {}

        html = resp.scrape_result.get("content", "")
        return self.extract_item_details(html)

    def extract_item_details(self, html):
        soup = BeautifulSoup(html, "html.parser")
        accordion_panels = soup.find_all("div", class_="accordionPanel")
        details = {}

        for panel in accordion_panels:
            for p in panel.find_all("p"):
                text = p.text.strip()
                if ":" in text:
                    key, value = map(str.strip, text.split(":", 1))
                    details[key.lower()] = value
                else:
                    if "description" not in details:
                        details["description"] = text
                    else:
                        details["description"] += f" {text}"
        return details

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
            "Designer": details.get("designers"),
            "Title": details.get("title"),
            "Stone": details.get("stone"),
            "Metal": details.get("metal"),
            "Box": details.get("box"),
            "Gender": details.get("gender")
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