#NO ITEM DETAILS FOUND
import random
import re
from urllib.parse import quote
from scrapfly import ScrapflyClient, ScrapeConfig, ScrapflyScrapeError
from bs4 import BeautifulSoup
from utils.utility import Utility
from utils.logger import get_logger
import asyncio
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

class RebagScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

        session = Session(profile_name="auricore-dev-kunal")
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
    giveup=lambda e: "OPERATION_TIMEOUT" not in str(e)  # retry only if it's timeout
)
    async def scrape_with_retries(self, config):
        self.logger.debug(f"⏳ Scraping URL: {config.url}")
        async with self.semaphore:
            return await self.client.async_scrape(config)

    async def search(self, query, category, product_id, image_query):
        self.logger.info(f"🔍 Searching Rebag for query: {query}")
        url = f"https://shop.rebag.com/collections/all?q={quote(query)}"

        try:
            resp = await self.scrape_with_retries(ScrapeConfig(
                url=url,
                render_js=True,
                asp=True,
                wait_for_selector="div.plp__product",
                session=Utility.generate_random_session(),
                retry=False,
                timeout=120000,
                country="US"
            ))
        except Exception as e:
            self.logger.exception("❌ Rebag scraping failed")
            return []

        soup = BeautifulSoup(resp.scrape_result["content"], "html.parser")
        cards = soup.select("div.plp__product")

        results = []

        for card in cards:
            try:
                anchor = card.find("a", class_="plp__card")
                if not anchor:
                    continue

                title_el = card.select_one(".products-carousel__card-title")
                brand_el = card.select_one(".products-carousel__card-designer")
                price_el = card.select_one(".rewards-plus-plp__product-price-value")
                image_el = card.select_one(".product-card-image__img, .products-carousel__image-wrapper img")

                title = f"{brand_el.get_text(strip=True)} {title_el.get_text(strip=True)}" if brand_el and title_el else None
                price = re.sub(r"[^\d.]", "", price_el.get_text(strip=True)) if price_el else None
                href = anchor["href"] if anchor.has_attr("href") else None
                image_url = "https:" + image_el["data-src"] if image_el and image_el.has_attr("data-src") else None

                if not (title and price and href):
                    continue

                full_url = href if href.startswith("http") else "https://shop.rebag.com" + href

                item = {
                    "title": title,
                    "listing_price": price,
                    "url": full_url,
                    "image_url": image_url,
                    "condition": "Used",
                    "seller": "Rebag Verified",
                    "shipping": "Free Shipping in US",
                    "location": "US",
                    "top_rated": True,
                    "source": "rebag",
                    "product_id": product_id,
                    "category": category,
                    "image_query": image_query
                }

                detail = await self.get_product_details(full_url)
                item["item_details"] = detail if isinstance(detail, dict) else {}

                # Insert into DB
                self.insert_or_update_product(item, category)

                results.append(item)

            except Exception as e:
                self.logger.warning(f"⚠️ Skipping Rebag item due to error: {e}")

        self.logger.info(f"✅ Rebag results fetched: {len(results)}")
        return results
    
    async def get_product_details(self, product_url):
        self.logger.info(f"🔍 Fetching product details from Rebag: {product_url}")
        try:
            resp = await self.scrape_with_retries(ScrapeConfig(
                url=product_url,
                render_js=True,
                asp=True,
                wait_for_selector=".pdp__container",
                session=Utility.generate_random_session(),
                retry=False,
                timeout=120000,
                country="US"
            ))
        except Exception as e:
            self.logger.exception("❌ Failed to fetch Rebag product detail page")
            return {}

        try:
            soup = BeautifulSoup(resp.scrape_result.get("content", ""), "html.parser")
            details = {}

            # Product details in bullet points
            bullet_section = soup.select_one(".accordion__body ul")
            if bullet_section:
                bullets = [li.get_text(strip=True) for li in bullet_section.select("li")]
                details["description"] = bullets

            # Extract dimensions or specific attributes if present
            attr_section = soup.select(".product-attributes__list li")
            for attr in attr_section:
                key = attr.select_one(".product-attributes__label")
                value = attr.select_one(".product-attributes__value")
                if key and value:
                    details[key.text.strip().lower().replace(" ", "_")] = value.text.strip()

            return details

        except Exception as e:
            self.logger.exception("❌ Failed to parse Rebag product detail content")
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
            "Designer": details.get("designers"),
            "Title": details.get("title"),
            "Stone": details.get("stone"),
            "Metal": details.get("metal"),
            "Box": details.get("box"),
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
